"""DB layer for the persistent item / daily_status store.

Backend is `libsql-client` (pure-Python HTTP client for Turso libSQL). The URL
scheme decides between remote and local:

- Production: TURSO_DATABASE_URL = `libsql://biz-support-<org>.turso.io`
                TURSO_AUTH_TOKEN = <jwt>
- Local dev:  TURSO_DATABASE_URL unset → fall back to `file:./state/biz_support.db`

The library lets us write identical SQL against either, so the rest of the
code never branches on backend.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import libsql_client
from libsql_client import Statement

from .models import Item

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DB_PATH = REPO_ROOT / "state" / "biz_support.db"


SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS item (
        stable_id     TEXT PRIMARY KEY,
        archive_key   TEXT NOT NULL,
        source_key    TEXT NOT NULL,
        first_seen    TEXT NOT NULL,
        last_seen     TEXT NOT NULL,
        title         TEXT NOT NULL,
        detail_url    TEXT NOT NULL,
        category      TEXT,
        organizer     TEXT,
        amount        TEXT,
        apply_period  TEXT,
        deadline      TEXT,
        region        TEXT,
        target        TEXT,
        summary       TEXT,
        badges_json   TEXT,
        extra_json    TEXT,
        active_since  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_status (
        snapshot_date TEXT NOT NULL,
        stable_id     TEXT NOT NULL,
        archive_key   TEXT NOT NULL,
        status        TEXT NOT NULL CHECK (status IN ('new','ongoing','expired')),
        PRIMARY KEY (snapshot_date, stable_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_daily_status_date    ON daily_status(snapshot_date)",
    "CREATE INDEX IF NOT EXISTS idx_daily_status_archive ON daily_status(archive_key, snapshot_date)",
    "CREATE INDEX IF NOT EXISTS idx_item_archive         ON item(archive_key)",
    "CREATE INDEX IF NOT EXISTS idx_item_last_seen       ON item(last_seen)",
    """
    CREATE TABLE IF NOT EXISTS scrape_run (
        snapshot_date        TEXT NOT NULL,
        archive_key          TEXT NOT NULL,
        status               TEXT NOT NULL CHECK (status IN ('running','complete','failed')),
        started_at           TEXT NOT NULL,
        completed_at         TEXT,
        baseline_date        TEXT,
        item_count           INTEGER NOT NULL DEFAULT 0,
        new_count            INTEGER NOT NULL DEFAULT 0,
        ongoing_count        INTEGER NOT NULL DEFAULT 0,
        expired_count        INTEGER NOT NULL DEFAULT 0,
        source_reports_json  TEXT,
        error                TEXT,
        PRIMARY KEY (snapshot_date, archive_key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_scrape_run_archive ON scrape_run(archive_key, snapshot_date)",
    """
    CREATE TABLE IF NOT EXISTS mail_delivery (
        delivery_date TEXT NOT NULL,
        archive_key   TEXT NOT NULL,
        message_id    TEXT NOT NULL,
        sent_at       TEXT NOT NULL,
        PRIMARY KEY (delivery_date, archive_key)
    )
    """,
)


# Retention window in days. Daily_status rows older than this are purged at the
# end of each run, along with item rows that no longer have any daily_status row.
RETENTION_DAYS = 100


def connect() -> libsql_client.ClientSync:
    """Open a sync libsql client. Production = Turso remote; dev = local SQLite file.

    Note: libsql-client defaults `libsql://` URLs to WebSocket (`wss://`) but
    Turso has deprecated WebSocket transport — connections come back as HTTP
    505. We force HTTP-only by rewriting `libsql://` → `https://` before
    handing the URL to the client."""
    url = os.environ.get("TURSO_DATABASE_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")
    if url and token:
        if url.startswith("libsql://"):
            url = "https://" + url[len("libsql://"):]
        return libsql_client.create_client_sync(url=url, auth_token=token)
    # Local fallback: ensure the parent dir exists (state/) since libsql-client
    # doesn't create it on its own.
    LOCAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    local_url = f"file:{LOCAL_DB_PATH}"
    return libsql_client.create_client_sync(url=local_url)


def migrate(client: libsql_client.ClientSync) -> None:
    """Idempotent CREATE TABLE / INDEX. Safe to call on every run."""
    for stmt in SCHEMA_STATEMENTS:
        client.execute(stmt)
    # ALTER TABLE for the extra_json column on pre-existing DBs (CREATE above
    # already includes it for fresh DBs, but Turso's tables created before
    # older deployments need this addition. Idempotent: ignore duplicate errors.
    # KeyError('result') swallowed too — Turso's hrana response for ALTER on
    # an already-present column omits the `result` field and libsql-client's
    # parser raises. The end state is what we want either way.
    for column_sql in (
        "ALTER TABLE item ADD COLUMN extra_json TEXT",
        "ALTER TABLE item ADD COLUMN active_since TEXT",
    ):
        try:
            client.execute(column_sql)
        except KeyError:
            pass  # already added; hrana parser quirk
        except Exception as e:
            msg = str(e).lower()
            if "duplicate" in msg or "already exists" in msg:
                pass
            else:
                raise

    # Existing deployments predate scrape_run. Treat their historical
    # daily_status snapshots as complete so the first upgraded run can diff
    # against the latest real snapshot instead of classifying everything new.
    now = datetime.now(UTC).isoformat()
    client.execute(
        "INSERT OR IGNORE INTO scrape_run "
        "(snapshot_date, archive_key, status, started_at, completed_at, item_count, "
        " new_count, ongoing_count, expired_count) "
        "SELECT snapshot_date, archive_key, 'complete', ?, ?, COUNT(*), "
        " SUM(CASE WHEN status='new' THEN 1 ELSE 0 END), "
        " SUM(CASE WHEN status='ongoing' THEN 1 ELSE 0 END), "
        " SUM(CASE WHEN status='expired' THEN 1 ELSE 0 END) "
        "FROM daily_status GROUP BY snapshot_date, archive_key",
        (now, now),
    )
    client.execute("UPDATE item SET active_since=first_seen WHERE active_since IS NULL")


# ── seed / bootstrap helpers ────────────────────────────────────────────────

def insert_seed_snapshot(
    client: libsql_client.ClientSync,
    archive_key: str,
    snapshot_date: date,
    items: list[Item],
) -> int:
    """Bulk-load items into `item` (UPSERT) and `daily_status` (status='ongoing')
    for a single archive on a single date. Used by `--seed` and the one-shot
    JSON-to-DB migration. Returns the number of items inserted."""
    if not items:
        return 0
    snap_iso = snapshot_date.isoformat()

    # Use `batch` (single round-trip, atomic on the server) instead of
    # client.transaction(): the HTTP hrana transport doesn't support
    # interactive transactions, and Turso has retired WebSocket.
    stmts: list[Statement] = []
    for it in items:
        stmts.append(Statement(
            _UPSERT_ITEM_SQL,
            _item_row(it, archive_key, first_seen=snap_iso, last_seen=snap_iso),
        ))
        stmts.append(Statement(
            "INSERT OR REPLACE INTO daily_status "
            "(snapshot_date, stable_id, archive_key, status) "
            "VALUES (?, ?, ?, 'ongoing')",
            (snap_iso, it.stable_id, archive_key),
        ))
    _batch_chunked(client, stmts)
    now = datetime.now(UTC).isoformat()
    client.execute(
        "INSERT OR REPLACE INTO scrape_run "
        "(snapshot_date, archive_key, status, started_at, completed_at, item_count, "
        " new_count, ongoing_count, expired_count) "
        "VALUES (?, ?, 'complete', ?, ?, ?, 0, ?, 0)",
        (snap_iso, archive_key, now, now, len(items), len(items)),
    )
    return len(items)


# ── daily flow helpers ──────────────────────────────────────────────────────

def get_active_snapshot_ids(
    client: libsql_client.ClientSync,
    archive_key: str,
    snapshot_date: date,
) -> set[str]:
    """Return stable_ids active in the supplied completed baseline snapshot."""
    result = client.execute(
        "SELECT stable_id FROM daily_status "
        "WHERE archive_key=? AND snapshot_date=? AND status IN ('new','ongoing')",
        (archive_key, snapshot_date.isoformat()),
    )
    return {row[0] for row in result.rows}


def get_latest_completed_snapshot_date(
    client: libsql_client.ClientSync,
    archive_key: str,
    before: date,
) -> date | None:
    """Return the latest completed snapshot strictly before ``before``."""
    result = client.execute(
        "SELECT MAX(snapshot_date) FROM scrape_run "
        "WHERE archive_key=? AND status='complete' AND snapshot_date < ?",
        (archive_key, before.isoformat()),
    )
    raw = result.rows[0][0] if result.rows and result.rows[0][0] else None
    return date.fromisoformat(raw) if raw else None


def start_scrape_run(
    client: libsql_client.ClientSync,
    archive_key: str,
    snapshot_date: date,
    baseline_date: date | None,
) -> None:
    now = datetime.now(UTC).isoformat()
    client.execute(
        "INSERT INTO scrape_run "
        "(snapshot_date, archive_key, status, started_at, baseline_date) "
        "VALUES (?, ?, 'running', ?, ?) "
        "ON CONFLICT(snapshot_date, archive_key) DO UPDATE SET "
        "status='running', started_at=excluded.started_at, completed_at=NULL, "
        "baseline_date=excluded.baseline_date, source_reports_json=NULL, error=NULL",
        (
            snapshot_date.isoformat(), archive_key, now,
            baseline_date.isoformat() if baseline_date else None,
        ),
    )


def fail_scrape_run(
    client: libsql_client.ClientSync,
    archive_key: str,
    snapshot_date: date,
    error: str,
) -> None:
    client.execute(
        "UPDATE scrape_run SET status='failed', completed_at=?, error=? "
        "WHERE snapshot_date=? AND archive_key=?",
        (
            datetime.now(UTC).isoformat(), error[:2000],
            snapshot_date.isoformat(), archive_key,
        ),
    )


def get_scrape_run(
    client: libsql_client.ClientSync,
    archive_key: str,
    snapshot_date: date,
) -> dict | None:
    result = client.execute(
        "SELECT status, baseline_date, item_count, new_count, ongoing_count, "
        "expired_count, source_reports_json, error FROM scrape_run "
        "WHERE snapshot_date=? AND archive_key=?",
        (snapshot_date.isoformat(), archive_key),
    )
    if not result.rows:
        return None
    row = list(result.rows[0])
    reports = []
    if row[6]:
        try:
            reports = json.loads(row[6])
        except (TypeError, ValueError):
            reports = []
    return {
        "status": row[0], "baseline_date": row[1], "item_count": int(row[2] or 0),
        "new_count": int(row[3] or 0), "ongoing_count": int(row[4] or 0),
        "expired_count": int(row[5] or 0), "source_reports": reports, "error": row[7],
    }


def get_mail_delivery(
    client: libsql_client.ClientSync,
    archive_key: str,
    delivery_date: date,
) -> str | None:
    """Return the durable SMTP Message-ID for an already-sent archive."""
    result = client.execute(
        "SELECT message_id FROM mail_delivery WHERE delivery_date=? AND archive_key=?",
        (delivery_date.isoformat(), archive_key),
    )
    return str(result.rows[0][0]) if result.rows else None


def record_mail_delivery(
    client: libsql_client.ClientSync,
    archive_key: str,
    delivery_date: date,
    message_id: str,
) -> None:
    """Persist successful SMTP delivery before the Git state marker is written."""
    client.execute(
        "INSERT INTO mail_delivery (delivery_date, archive_key, message_id, sent_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(delivery_date, archive_key) DO UPDATE SET "
        "message_id=excluded.message_id, sent_at=excluded.sent_at",
        (
            delivery_date.isoformat(), archive_key, message_id,
            datetime.now(UTC).isoformat(),
        ),
    )


def record_daily(
    client: libsql_client.ClientSync,
    archive_key: str,
    today: date,
    items_today: list[Item],
    new_ids: set[str],
    ongoing_ids: set[str],
    expired_ids: set[str],
    source_reports: list[dict] | None = None,
) -> None:
    """Upsert today's items into `item` and write the three statuses into
    `daily_status`. Idempotent: REPLACE on (snapshot_date, stable_id)."""
    today_iso = today.isoformat()
    today_by_id = {i.stable_id: i for i in items_today}

    # `batch` instead of `transaction()` — HTTP hrana doesn't support
    # interactive transactions (Turso has retired WebSocket).
    stmts: list[Statement] = []
    # Clear today's prior status rows first. If a later batch fails, scrape_run
    # remains non-complete and the mail stage refuses to consume the partial
    # snapshot. A same-day retry therefore cannot retain stale "new" rows.
    client.execute(
        "DELETE FROM daily_status WHERE snapshot_date=? AND archive_key=?",
        (today_iso, archive_key),
    )

    for sid in new_ids | ongoing_ids:
        it = today_by_id[sid]
        active_since = today if sid in new_ids else it.active_since
        # For brand-new items, first_seen = today. For ongoing, leave
        # first_seen alone (ON CONFLICT DO UPDATE doesn't touch it).
        stmts.append(Statement(
            _UPSERT_ITEM_SQL,
            _item_row(
                it, archive_key, first_seen=today_iso, last_seen=today_iso,
                active_since=active_since.isoformat() if active_since else None,
            ),
        ))

    status_rows: list[tuple] = []
    status_rows.extend((today_iso, sid, archive_key, "new")      for sid in new_ids)
    status_rows.extend((today_iso, sid, archive_key, "ongoing")  for sid in ongoing_ids)
    status_rows.extend((today_iso, sid, archive_key, "expired")  for sid in expired_ids)
    for row in status_rows:
        stmts.append(Statement(
            "INSERT OR REPLACE INTO daily_status "
            "(snapshot_date, stable_id, archive_key, status) VALUES (?, ?, ?, ?)",
            row,
        ))
    _batch_chunked(client, stmts)
    client.execute(
        "UPDATE scrape_run SET status='complete', completed_at=?, item_count=?, "
        "new_count=?, ongoing_count=?, expired_count=?, source_reports_json=?, error=NULL "
        "WHERE snapshot_date=? AND archive_key=?",
        (
            datetime.now(UTC).isoformat(), len(items_today), len(new_ids),
            len(ongoing_ids), len(expired_ids),
            json.dumps(source_reports or [], ensure_ascii=False), today_iso, archive_key,
        ),
    )


def fetch_expired_items(
    client: libsql_client.ClientSync,
    archive_key: str,
    today: date,
) -> list[Item]:
    """Resolve today's expired stable_ids back to full Item rows. The renderer
    needs full metadata to show the 🔚 종료 section (expired items aren't in
    today's scrape so we read them from `item`)."""
    result = client.execute(
        "SELECT i.stable_id, i.source_key, i.title, i.detail_url, i.category, "
        "i.organizer, i.amount, i.apply_period, i.deadline, i.region, i.target, "
        "i.summary, i.badges_json, i.first_seen, i.active_since "
        "FROM daily_status d JOIN item i USING (stable_id) "
        "WHERE d.snapshot_date = ? AND d.archive_key = ? AND d.status = 'expired'",
        (today.isoformat(), archive_key),
    )
    return [_item_from_db_row(r) for r in result.rows]


def backfill_item_enrichment(
    client: libsql_client.ClientSync,
    stable_id: str,
    *,
    badges_json: str | None,
    summary: str | None = None,
    region: str | None = None,
    target: str | None = None,
    amount: str | None = None,
) -> None:
    """One-shot UPDATE used by the backfill flow. Unlike record_daily's
    UPSERT (which COALESCEs badges_json to PROTECT existing values), this
    function FORCIBLY overwrites badges_json with whatever the detail-page
    enrichment produced. The other fields are only set when non-NULL — we
    don't blank out, e.g., a region that detail-page parsing happened to
    miss but the LIST page had.

    Used by `cli.py --backfill-bizinfo-api` to populate API-derived enrichment
    and GPU·AI badges on legacy items."""
    client.execute(
        "UPDATE item SET "
        "badges_json = ?, "
        "summary = COALESCE(?, summary), "
        "region = COALESCE(?, region), "
        "target = COALESCE(?, target), "
        "amount = COALESCE(?, amount) "
        "WHERE stable_id = ?",
        (badges_json, summary, region, target, amount, stable_id),
    )


def prune(client: libsql_client.ClientSync, today: date) -> tuple[int, int]:
    """Delete daily_status rows older than RETENTION_DAYS, then drop item rows
    that no longer have any daily_status row pointing at them.
    Returns (daily_status_deleted, item_deleted)."""
    cutoff = (today - timedelta(days=RETENTION_DAYS)).isoformat()

    r1 = client.execute("DELETE FROM daily_status WHERE snapshot_date < ?", (cutoff,))
    client.execute("DELETE FROM scrape_run WHERE snapshot_date < ?", (cutoff,))
    r2 = client.execute(
        "DELETE FROM item WHERE stable_id NOT IN (SELECT DISTINCT stable_id FROM daily_status)"
    )
    # libsql ResultSet exposes rows_affected as the count of mutated rows.
    return (int(r1.rows_affected or 0), int(r2.rows_affected or 0))


# ── internals ───────────────────────────────────────────────────────────────

# Per-batch statement cap. Turso's hrana batch endpoint has a documented soft
# limit; staying under a few hundred per request keeps each round trip well
# under the timeout and avoids 'request too large' on big initial seeds
# (gov-support is 611 items × 2 stmts = 1222 → split into chunks).
_BATCH_CHUNK = 200


def _batch_chunked(client: libsql_client.ClientSync, stmts: list[Statement]) -> None:
    """Execute `stmts` in size-capped batches. Each chunk is atomic at the
    server (libsql batch returns all-or-none for that chunk). Across chunks
    we are NOT transactional — partial failure leaves the DB in a half-written
    state, but our writes (INSERT OR REPLACE / UPSERT) are idempotent so the
    next run resolves it."""
    for start in range(0, len(stmts), _BATCH_CHUNK):
        chunk = stmts[start : start + _BATCH_CHUNK]
        if chunk:
            client.batch(chunk)


_UPSERT_ITEM_SQL = (
    "INSERT INTO item ("
    "stable_id, archive_key, source_key, first_seen, last_seen, "
    "title, detail_url, category, organizer, amount, apply_period, deadline, "
    "region, target, summary, badges_json, extra_json, active_since"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(stable_id) DO UPDATE SET "
    "last_seen=excluded.last_seen, "
    "title=excluded.title, "
    "detail_url=excluded.detail_url, "
    # COALESCE: preserve an existing non-NULL category. K-Startup adapters
    # assign default categories ('mentoring' / 'facility') that don't match
    # the finer legacy categorization (rnd vs mentoring, facility vs global);
    # we don't want today's scrape to clobber yesterday's better data.
    "category=COALESCE(item.category, excluded.category), "
    "organizer=excluded.organizer, "
    "amount=COALESCE(excluded.amount, item.amount), "
    "apply_period=excluded.apply_period, "
    "deadline=excluded.deadline, "
    "region=COALESCE(excluded.region, item.region), "
    "target=COALESCE(excluded.target, item.target), "
    "summary=COALESCE(excluded.summary, item.summary), "
    # Bizinfo list pages lack detail-derived badges. Preserve the previously
    # enriched value when a later list-only scrape has no replacement.
    "badges_json=COALESCE(item.badges_json, excluded.badges_json), "
    # Same protection for extra_json — populated on the FIRST detail fetch
    # for a new stable_id, then never overwritten on subsequent list scrapes.
    "extra_json=COALESCE(item.extra_json, excluded.extra_json), "
    "active_since=COALESCE(excluded.active_since, item.active_since)"
)


_DEFAULT_ACTIVE_SINCE = object()


def _item_row(
    it: Item,
    archive_key: str,
    *,
    first_seen: str,
    last_seen: str,
    active_since: str | None | object = _DEFAULT_ACTIVE_SINCE,
) -> tuple:
    if active_since is _DEFAULT_ACTIVE_SINCE:
        active_since_value = it.active_since.isoformat() if it.active_since else first_seen
    else:
        active_since_value = active_since
    return (
        it.stable_id,
        archive_key,
        it.source_key,
        first_seen,
        last_seen,
        it.title,
        it.detail_url,
        it.category,
        it.organizer,
        it.amount,
        it.apply_period,
        it.deadline.isoformat() if it.deadline else None,
        it.region,
        it.target,
        it.summary,
        json.dumps(list(it.badges), ensure_ascii=False) if it.badges else None,
        # extra_json: reserved for future use (cached detail-page payload, etc.).
        # Always NULL today; COALESCE in UPSERT preserves any existing value.
        None,
        active_since_value,
    )


def _item_from_db_row(row: Iterable) -> Item:
    values = list(row)
    if len(values) == 13:
        values.extend((None, None))
    (stable_id, source_key, title, detail_url, category, organizer,
     amount, apply_period, deadline_iso, region, target, summary, badges_json,
     first_seen_iso, active_since_iso) = values
    deadline_val: date | None = None
    if deadline_iso:
        try:
            deadline_val = date.fromisoformat(deadline_iso)
        except ValueError:
            deadline_val = None
    badges_tuple: tuple[str, ...] = ()
    if badges_json:
        try:
            badges_tuple = tuple(json.loads(badges_json))
        except (TypeError, ValueError):
            badges_tuple = ()
    first_seen_val = _parse_db_date(first_seen_iso)
    active_since_val = _parse_db_date(active_since_iso)
    return Item(
        stable_id=stable_id,
        source_key=source_key,
        title=title,
        detail_url=detail_url,
        category=category,
        organizer=organizer,
        amount=amount,
        apply_period=apply_period,
        deadline=deadline_val,
        region=region,
        target=target,
        summary=summary,
        badges=badges_tuple,
        first_seen=first_seen_val,
        active_since=active_since_val,
    )


def _parse_db_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
