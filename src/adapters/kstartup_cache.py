"""Shared completed-snapshot fallback helper for archive adapters.

A list fetch that fails or stops partway through must not be treated as
today's real (small) list: the diff classifier would mark every un-scraped
item as ended, then new again once the source recovers, even though the source
did not actually change.

kstartup-mentoring/global each merge TWO endpoints into one archive, each
endpoint owning a fixed `category` ('rnd'/'mentoring', 'facility'/'global').
A failure on ONE endpoint must fall back to only THAT endpoint's last cached
slice — not the whole archive — so the endpoint that succeeded keeps its
fresh data. `category=None` (kstartup-biz's single-endpoint case) matches
every item for that archive+source with no category filter."""

from __future__ import annotations

from .. import db
from ..models import Item


def load_cached_items(
    archive_key: str, source_key: str, category: str | None = None,
) -> list[Item]:
    """Return the last successfully-scraped snapshot for `archive_key` +
    `source_key` (optionally scoped to one `category`), whichever day that
    was. SAFE: closes its own connection."""
    client = db.connect()
    try:
        db.migrate(client)
        where = ["d.archive_key = ?", "i.source_key = ?"]
        params: list[str] = [archive_key, source_key]
        if category is not None:
            where.append("i.category = ?")
            params.append(category)
        where_sql = " AND ".join(where)

        max_params = list(params)
        result = client.execute(
            "SELECT i.stable_id, i.source_key, i.title, i.detail_url, i.category, "
            "i.organizer, i.amount, i.apply_period, i.deadline, i.region, i.target, "
            "i.summary, i.badges_json, i.first_seen, i.active_since "
            "FROM daily_status d JOIN item i USING (stable_id) "
            "JOIN scrape_run r ON r.snapshot_date=d.snapshot_date "
            " AND r.archive_key=d.archive_key "
            f"WHERE {where_sql} AND d.status IN ('new','ongoing') "
            "AND r.status='complete' AND d.snapshot_date=("
            "  SELECT MAX(d.snapshot_date) FROM daily_status d "
            "  JOIN item i USING (stable_id) "
            "  JOIN scrape_run r ON r.snapshot_date=d.snapshot_date "
            "   AND r.archive_key=d.archive_key "
            f"  WHERE {where_sql} AND d.status IN ('new','ongoing') "
            "   AND r.status='complete'"
            ")",
            params + max_params,
        )
        return [db._item_from_db_row(r) for r in result.rows]
    finally:
        client.close()
