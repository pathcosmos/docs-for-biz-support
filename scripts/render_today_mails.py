"""Render today's mail HTML for all 5 archives from the DB-seeded items.

Until real scrapers exist, this treats the most recent daily_status snapshot's
items as 'ongoing' (no fake new/expired). Output goes to
mails/<archive>/YYYY-MM-DD.html for local verification. Both local SQLite
(dev) and Turso remote (prod) work — `db.connect()` picks based on env."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import db  # type: ignore
from src.config.archives import ARCHIVE_ORDER, ARCHIVES
from src.render.daily_html import render_daily_html


def _load_latest_items(client, archive_key: str):
    """Return (snapshot_date, items[]) for the most recent daily_status
    snapshot in this archive. Used for local preview before real scrapers."""
    latest = client.execute(
        "SELECT MAX(snapshot_date) FROM daily_status WHERE archive_key = ?",
        (archive_key,),
    )
    rows = latest.rows
    if not rows or rows[0][0] is None:
        return None, []
    snap = rows[0][0]
    result = client.execute(
        "SELECT i.stable_id, i.source_key, i.title, i.detail_url, i.category, "
        "i.organizer, i.amount, i.apply_period, i.deadline, i.region, i.target, "
        "i.summary, i.badges_json "
        "FROM daily_status d JOIN item i USING (stable_id) "
        "WHERE d.snapshot_date = ? AND d.archive_key = ?",
        (snap, archive_key),
    )
    return snap, [db._item_from_db_row(r) for r in result.rows]


def main() -> int:
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    out_root = Path(__file__).resolve().parents[1] / "mails"
    out_root.mkdir(exist_ok=True)

    client = db.connect()
    try:
        db.migrate(client)
        missing: list[str] = []
        for key in ARCHIVE_ORDER:
            cfg = ARCHIVES[key]
            snap, items = _load_latest_items(client, key)
            if not items:
                missing.append(key)
                continue
            html = render_daily_html(
                cfg=cfg,
                items_new=[],
                items_ongoing=items,
                today=today,
                for_email=True,
            )
            out_dir = out_root / key
            out_dir.mkdir(exist_ok=True)
            out_path = out_dir / f"{today.isoformat()}.html"
            out_path.write_text(html, encoding="utf-8")
            print(f"  {key}: {len(items)} items (snap={snap}) → "
                  f"{out_path}  ({len(html):,} bytes)")
    finally:
        client.close()

    if missing:
        print(f"\n⚠  no DB rows for: {', '.join(missing)} — run `python -m src.cli --seed` first")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
