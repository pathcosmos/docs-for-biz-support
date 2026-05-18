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

import os
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

import libsql_client

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
        badges_json   TEXT
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
)


# Retention window in days. Daily_status rows older than this are purged at the
# end of each run, along with item rows that no longer have any daily_status row.
RETENTION_DAYS = 100


def connect() -> libsql_client.ClientSync:
    """Open a sync libsql client. Production = Turso remote; dev = local SQLite file."""
    url = os.environ.get("TURSO_DATABASE_URL")
    token = os.environ.get("TURSO_AUTH_TOKEN")
    if url and token:
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

    # libsql-client's TransactionSync context manager only close()s on exit —
    # it does NOT auto-commit. Call commit() explicitly inside the block;
    # if any execute() raises, the with-block exits without commit and the
    # transaction is closed (rolled back) by close().
    tx = client.transaction()
    try:
        for it in items:
            tx.execute(
                _UPSERT_ITEM_SQL,
                _item_row(it, archive_key, first_seen=snap_iso, last_seen=snap_iso),
            )
            tx.execute(
                "INSERT OR REPLACE INTO daily_status "
                "(snapshot_date, stable_id, archive_key, status) "
                "VALUES (?, ?, ?, 'ongoing')",
                (snap_iso, it.stable_id, archive_key),
            )
        tx.commit()
    finally:
        tx.close()
    return len(items)


# ── daily flow helpers (used by PR3+ diff.py) ───────────────────────────────

def get_yesterday_ids(
    client: libsql_client.ClientSync,
    archive_key: str,
    yesterday: date,
) -> set[str]:
    """Return the set of stable_ids that were active (new or ongoing) yesterday
    for the given archive. Used to classify today's scrape into new/ongoing/expired."""
    result = client.execute(
        "SELECT stable_id FROM daily_status "
        "WHERE archive_key=? AND snapshot_date=? AND status IN ('new','ongoing')",
        (archive_key, yesterday.isoformat()),
    )
    return {row[0] for row in result.rows}


def record_daily(
    client: libsql_client.ClientSync,
    archive_key: str,
    today: date,
    items_today: list[Item],
    new_ids: set[str],
    ongoing_ids: set[str],
    expired_ids: set[str],
) -> None:
    """Upsert today's items into `item` and write the three statuses into
    `daily_status`. Idempotent: REPLACE on (snapshot_date, stable_id)."""
    today_iso = today.isoformat()
    today_by_id = {i.stable_id: i for i in items_today}

    tx = client.transaction()
    try:
        for sid in new_ids | ongoing_ids:
            it = today_by_id[sid]
            # For brand-new items, first_seen = today. For ongoing, leave
            # first_seen alone (ON CONFLICT DO UPDATE doesn't touch it).
            tx.execute(
                _UPSERT_ITEM_SQL,
                _item_row(it, archive_key, first_seen=today_iso, last_seen=today_iso),
            )

        status_rows: list[tuple] = []
        status_rows.extend((today_iso, sid, archive_key, "new")      for sid in new_ids)
        status_rows.extend((today_iso, sid, archive_key, "ongoing")  for sid in ongoing_ids)
        status_rows.extend((today_iso, sid, archive_key, "expired")  for sid in expired_ids)
        for row in status_rows:
            tx.execute(
                "INSERT OR REPLACE INTO daily_status "
                "(snapshot_date, stable_id, archive_key, status) VALUES (?, ?, ?, ?)",
                row,
            )
        tx.commit()
    finally:
        tx.close()


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
        "i.summary, i.badges_json "
        "FROM daily_status d JOIN item i USING (stable_id) "
        "WHERE d.snapshot_date = ? AND d.archive_key = ? AND d.status = 'expired'",
        (today.isoformat(), archive_key),
    )
    return [_item_from_db_row(r) for r in result.rows]


def prune(client: libsql_client.ClientSync, today: date) -> tuple[int, int]:
    """Delete daily_status rows older than RETENTION_DAYS, then drop item rows
    that no longer have any daily_status row pointing at them.
    Returns (daily_status_deleted, item_deleted)."""
    cutoff = (today - timedelta(days=RETENTION_DAYS)).isoformat()

    r1 = client.execute("DELETE FROM daily_status WHERE snapshot_date < ?", (cutoff,))
    r2 = client.execute(
        "DELETE FROM item WHERE stable_id NOT IN (SELECT DISTINCT stable_id FROM daily_status)"
    )
    # libsql ResultSet exposes rows_affected as the count of mutated rows.
    return (int(r1.rows_affected or 0), int(r2.rows_affected or 0))


# ── internals ───────────────────────────────────────────────────────────────

_UPSERT_ITEM_SQL = (
    "INSERT INTO item ("
    "stable_id, archive_key, source_key, first_seen, last_seen, "
    "title, detail_url, category, organizer, amount, apply_period, deadline, "
    "region, target, summary, badges_json"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
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
    "amount=excluded.amount, "
    "apply_period=excluded.apply_period, "
    "deadline=excluded.deadline, "
    "region=excluded.region, "
    "target=excluded.target, "
    "summary=excluded.summary, "
    "badges_json=excluded.badges_json"
)


def _item_row(it: Item, archive_key: str, *, first_seen: str, last_seen: str) -> tuple:
    import json
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
    )


def _item_from_db_row(row: Iterable) -> Item:
    import json
    (stable_id, source_key, title, detail_url, category, organizer,
     amount, apply_period, deadline_iso, region, target, summary, badges_json) = list(row)
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
    )
