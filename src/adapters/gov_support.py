"""Adapter for the gov-support archive — multi-source aggregator.

Current sources (PR-GPU as of 2026-05-19):
- bizinfo (기업마당) — main, ~1377 items/day from the LIST page. NEW items
  trigger an additional DETAIL page fetch to enrich summary + hashtags + the
  `🖥️ GPU·AI 인프라` badge via keyword matching.
- NIPA (정보통신산업진흥원) — ~24 AI/digital programs. All get the GPU·AI
  badge auto-applied (entire site is AI-focused).

Deferred (PR8): IRIS, NTIS, smes, smart-factory, 6 technoparks.

Sorting: items by deadline ascending; items with no deadline last. Most
time-sensitive announcements at the top of the mail body — survives Gmail's
~102 KB inbox-view clip.

Detail-fetch policy: only NEW items (stable_id not in yesterday's DB) get
the DETAIL fetch. Items already in DB rely on the DB's previously-cached
extras (which COALESCE preserves on re-upsert). The fetch budget is ~20-50
new items/day × 1 s = ~30 s — safe within the 30 min cron timeout.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from . import register
from .. import db
from ..config.archives import ARCHIVES
from ..models import Item
from ..render.labels import assign_badges, GPU_AI_BADGE
from ..scrapers.base import HttpClient, ScrapeError
from ..scrapers.bizinfo import (
    BizinfoDetail, BizinfoRaw, detail_url as bizinfo_detail_url,
    fetch_detail as bizinfo_fetch_detail,
    fetch_listings as fetch_bizinfo,
)
from ..scrapers.nipa import NipaRaw, detail_url as nipa_detail_url, fetch_listings as fetch_nipa


logger = logging.getLogger(__name__)

SOURCE_KEY_BIZINFO = "bizinfo"
SOURCE_KEY_NIPA = "nipa"
_FAR_FUTURE = date(9999, 12, 31)


@register("gov-support")
def fetch() -> list[Item]:
    """Aggregate sources, then enrich NEW bizinfo items via DETAIL fetch,
    then sort by deadline. Caller is the scrape stage which feeds the result
    into diff + DB write."""
    items: list[Item] = []
    with HttpClient() as client:
        # --- 1. bizinfo list ---
        bizinfo_fallback_items: list[Item] = []
        try:
            bizinfo_result = fetch_bizinfo(client)
        except ScrapeError as e:
            # bizinfo.go.kr fails intermittently (5xx/timeout from GH Actions'
            # IP range). Without a fallback, today's scrape would report only
            # NIPA's ~10 items, so diff.classify() would mark all ~1400 open
            # bizinfo programs as 🔚종료 today, then 🆕신규 again the day
            # bizinfo recovers — a false churn cycle with nothing actually
            # having changed. Re-feed the last successfully-scraped snapshot
            # instead so those items classify as 진행중 through the outage.
            logger.warning(
                "bizinfo list failed: %s — falling back to last cached snapshot", e,
            )
            bizinfo_raws = []
            bizinfo_fallback_items = _load_cached_bizinfo_items()
        else:
            if bizinfo_result.complete:
                bizinfo_raws = bizinfo_result.items
            else:
                # Pagination stopped partway through — trusting this as
                # "today's full list" would make every un-scraped item look
                # 🔚종료 in the diff, same corruption a total failure causes.
                # Fall back exactly like a total failure rather than shipping
                # a partial scrape as if it were complete.
                logger.warning(
                    "bizinfo list incomplete (%d of an unknown total collected) "
                    "— falling back to last cached snapshot instead of a "
                    "partial scrape", len(bizinfo_result.items),
                )
                bizinfo_raws = []
                bizinfo_fallback_items = _load_cached_bizinfo_items()

        # --- 2. NIPA ---
        try:
            nipa_raws = fetch_nipa(client)
        except ScrapeError as e:
            logger.warning("nipa list failed: %s", e)
            nipa_raws = []

        # --- 3. Identify which bizinfo stable_ids are NEW (need detail fetch).
        # Look up existing stable_ids in the DB; today's scrape is "new" for
        # any id not already there. We do this in a single SELECT regardless
        # of how many ids we have (Turso handles it).
        bizinfo_ids = {f"{SOURCE_KEY_BIZINFO}:{r.pblanc_id}" for r in bizinfo_raws}
        existing = _lookup_existing(bizinfo_ids) if bizinfo_ids else set()
        new_bizinfo_ids = bizinfo_ids - existing
        if new_bizinfo_ids:
            logger.info(
                "gov-support: %d total bizinfo items, %d new → detail fetch",
                len(bizinfo_raws), len(new_bizinfo_ids),
            )

        # --- 4. Fetch DETAIL for new bizinfo items (one HTTP call per item).
        # Failure of one detail doesn't break the run — we just lose that
        # item's enrichment.
        details_by_id: dict[str, BizinfoDetail] = {}
        for raw in bizinfo_raws:
            stable_id = f"{SOURCE_KEY_BIZINFO}:{raw.pblanc_id}"
            if stable_id not in new_bizinfo_ids:
                continue
            try:
                details_by_id[raw.pblanc_id] = bizinfo_fetch_detail(client, raw.pblanc_id)
            except ScrapeError as e:
                logger.warning("bizinfo detail %s failed: %s", raw.pblanc_id, e)

    # --- 5. Convert raws to Items
    items.extend(_from_bizinfo(r, details_by_id.get(r.pblanc_id)) for r in bizinfo_raws)
    items.extend(bizinfo_fallback_items)
    items.extend(_from_nipa(r) for r in nipa_raws)

    # --- 6. Sort by deadline asc, None last (stable for ties)
    items.sort(key=lambda i: i.deadline or _FAR_FUTURE)
    return items


def _load_cached_bizinfo_items() -> list[Item]:
    """Fallback for when the live bizinfo list fetch fails: return the last
    successfully-scraped bizinfo snapshot from the DB (whichever day that
    was — `last_seen` only advances on a day bizinfo actually succeeded, so
    this is stable across consecutive outage days). SAFE: closes its own
    connection."""
    client = db.connect()
    try:
        db.migrate(client)
        result = client.execute(
            "SELECT stable_id, source_key, title, detail_url, category, "
            "organizer, amount, apply_period, deadline, region, target, "
            "summary, badges_json FROM item "
            "WHERE archive_key = 'gov-support' AND source_key = ? AND last_seen = ("
            "  SELECT MAX(last_seen) FROM item "
            "  WHERE archive_key = 'gov-support' AND source_key = ?"
            ")",
            (SOURCE_KEY_BIZINFO, SOURCE_KEY_BIZINFO),
        )
        return [db._item_from_db_row(r) for r in result.rows]
    finally:
        client.close()


def _lookup_existing(stable_ids: set[str]) -> set[str]:
    """Return the subset of `stable_ids` already in the DB. Used to decide
    which need a DETAIL fetch. SAFE: closes its own connection."""
    if not stable_ids:
        return set()
    client = db.connect()
    try:
        db.migrate(client)
        out: set[str] = set()
        # Batch the IN clause to keep parameter count under 999 (SQLite cap).
        batch = 500
        id_list = list(stable_ids)
        for start in range(0, len(id_list), batch):
            chunk = id_list[start : start + batch]
            placeholders = ",".join("?" * len(chunk))
            r = client.execute(
                f"SELECT stable_id FROM item WHERE stable_id IN ({placeholders})",
                chunk,
            )
            out.update(row[0] for row in r.rows)
        return out
    finally:
        client.close()


def _from_bizinfo(r: BizinfoRaw, det: BizinfoDetail | None) -> Item:
    """Bizinfo row → Item. When DETAIL data is present (NEW items), we merge
    in summary/hashtags and run the GPU·AI keyword matcher across title +
    summary + hashtags + organizer."""
    summary = det.summary if det else None
    hashtags = list(det.hashtags) if det else []
    region = (det.region if det else None) or None
    target = (det.target if det else None) or None
    amount = (det.amount if det else None) or None

    badges = assign_badges(
        title=r.title,
        summary=summary,
        hashtags=hashtags,
        organizer=r.organizer,
    )

    return Item(
        stable_id=f"{SOURCE_KEY_BIZINFO}:{r.pblanc_id}",
        source_key=SOURCE_KEY_BIZINFO,
        title=r.title,
        detail_url=bizinfo_detail_url(r.pblanc_id),
        category=None,
        organizer=r.organizer,
        amount=amount,
        apply_period=r.apply_period,
        deadline=r.deadline,
        region=region,
        target=target,
        summary=summary,
        badges=badges,
    )


def _from_nipa(r: NipaRaw) -> Item:
    """NIPA → Item. The agency itself is the AI/digital arm of MSIT, so we
    auto-assign the GPU·AI badge to every NIPA program. The keyword matcher
    still runs on title (it'll usually hit anyway) and is the source of
    truth — if a title is uncannily not-AI we don't lie."""
    badges = assign_badges(title=r.title, summary=None, organizer="NIPA")
    # If somehow the title doesn't trigger the matcher, force-add anyway —
    # the user has explicitly chosen NIPA as a curated GPU·AI source.
    if GPU_AI_BADGE not in badges:
        badges = badges + (GPU_AI_BADGE,)
    return Item(
        stable_id=f"{SOURCE_KEY_NIPA}:{r.bsns_id}",
        source_key=SOURCE_KEY_NIPA,
        title=r.title,
        detail_url=nipa_detail_url(r.bsns_id),
        category=None,
        organizer="NIPA",
        badges=badges,
    )
