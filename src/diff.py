"""Classify today's scraped items against the latest completed DB snapshot.

The trichotomy that lands in `daily_status`:
- new       — today_id is not in the baseline's active set
- ongoing   — today_id is in the baseline's active set
- expired   — a baseline id is not in today's scrape (record once, today)

Items that were `expired` in the baseline are deliberately excluded from its
set so they don't chain into another `expired` tick today. Re-appearances are
treated as `new` (simplest); first_seen is preserved on item upsert.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from . import db
from .config.archives import ArchiveConfig
from .models import Item


@dataclass
class DiffResult:
    new: list[Item]            # full Item rows (from today's scrape)
    ongoing: list[Item]        # full Item rows (from today's scrape)
    expired: list[Item]        # full Item rows (from DB, not today's scrape)

    @property
    def counts(self) -> tuple[int, int, int]:
        return (len(self.new), len(self.ongoing), len(self.expired))


def classify(
    client,
    cfg: ArchiveConfig,
    today: date,
    baseline_date: date | None,
    items_today: list[Item],
) -> DiffResult:
    """Read the baseline's active ids, partition today's scrape into
    new/ongoing, resolve baseline-only ids back to full Item rows for the
    `expired` bucket.

    `client` is a live libsql client. Caller is responsible for opening/closing.
    """
    today_by_id = {i.stable_id: i for i in items_today}
    baseline_ids = (
        db.get_active_snapshot_ids(client, cfg.key, baseline_date)
        if baseline_date else set()
    )

    new_ids = today_by_id.keys() - baseline_ids
    ongoing_ids = today_by_id.keys() & baseline_ids
    expired_ids = baseline_ids - today_by_id.keys()

    # Preserve adapter order (gov-support is deadline-sorted). Set iteration
    # would make otherwise identical renders reorder unpredictably.
    new_items = [item for item in items_today if item.stable_id in new_ids]
    ongoing_items = [item for item in items_today if item.stable_id in ongoing_ids]

    # Expired items aren't in today's scrape, so we need full Item rows from
    # the DB (joined via stable_id). `fetch_expired_items` reads daily_status
    # for today AFTER record_daily has been called — but here we want it BEFORE
    # that. Workaround: query item table directly by stable_id.
    expired_items = _fetch_items_by_ids(client, expired_ids)

    return DiffResult(new=new_items, ongoing=ongoing_items, expired=expired_items)


def _fetch_items_by_ids(client, ids: set[str]) -> list[Item]:
    if not ids:
        return []
    # SQLite's IN clause is limited to 999 parameters by default. We batch to
    # be safe for very large archives (gov-support hit 600+ on day 1 — well
    # under the limit, but cheap to chunk).
    out: list[Item] = []
    batch_size = 500
    id_list = list(ids)
    for start in range(0, len(id_list), batch_size):
        chunk = id_list[start : start + batch_size]
        placeholders = ",".join("?" * len(chunk))
        result = client.execute(
            "SELECT stable_id, source_key, title, detail_url, category, "
            "organizer, amount, apply_period, deadline, region, target, "
            "summary, badges_json, first_seen, active_since FROM item "
            "WHERE stable_id IN (" + placeholders + ")",
            chunk,
        )
        out.extend(db._item_from_db_row(r) for r in result.rows)
    return out
