"""Shared DB-fallback helper for the three K-Startup adapters (kstartup-biz/
mentoring/global). Same rationale as gov_support.py's bizinfo fallback: a
list fetch that fails or stops partway through must NOT be treated as
"today's real (small) list" — the diff classifier would mark every
un-scraped item as 🔚종료, then 🆕신규 again once the source recovers, with
nothing having actually changed on the source.

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
        where = ["archive_key = ?", "source_key = ?"]
        params: list[str] = [archive_key, source_key]
        if category is not None:
            where.append("category = ?")
            params.append(category)
        where_sql = " AND ".join(where)

        max_params = list(params)
        result = client.execute(
            "SELECT stable_id, source_key, title, detail_url, category, "
            "organizer, amount, apply_period, deadline, region, target, "
            "summary, badges_json FROM item "
            f"WHERE {where_sql} AND last_seen = ("
            f"  SELECT MAX(last_seen) FROM item WHERE {where_sql}"
            ")",
            params + max_params,
        )
        return [db._item_from_db_row(r) for r in result.rows]
    finally:
        client.close()
