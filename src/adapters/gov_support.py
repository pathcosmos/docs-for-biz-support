"""Adapter for the gov-support archive — multi-source aggregator.

Current sources:
- bizinfo (기업마당) — main source. NEW items
  trigger an additional DETAIL page fetch to enrich summary + hashtags + the
  `🖥️ GPU·AI 인프라` badge via keyword matching.
- NIPA (정보통신산업진흥원) — AI/digital programs. All get the GPU·AI
  badge auto-applied (entire site is AI-focused).
- IRIS (범부처통합연구지원시스템) — currently open R&D announcements.
- NTIS (국가과학기술지식정보서비스) — upcoming/open R&D announcements.

Sorting: items by deadline ascending; items with no deadline last. Most
time-sensitive announcements at the top of the mail body — survives Gmail's
~102 KB inbox-view clip.

Detail-fetch policy: only first-seen items (stable_id not yet in the DB) get
the DETAIL fetch. Items already in DB rely on the DB's previously-cached
extras (which COALESCE preserves on re-upsert). The fetch budget is ~20-50
new items/day × 1 s = ~30 s — safe within the 30 min cron timeout.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from .. import db
from ..models import AdapterResult, Item, SourceReport
from ..render.labels import GPU_AI_BADGE, assign_badges
from ..scrapers.base import HttpClient, ScrapeError
from ..scrapers.bizinfo import (
    BizinfoDetail,
    BizinfoRaw,
)
from ..scrapers.bizinfo import (
    detail_url as bizinfo_detail_url,
)
from ..scrapers.bizinfo import (
    fetch_detail as bizinfo_fetch_detail,
)
from ..scrapers.bizinfo import (
    fetch_listings as fetch_bizinfo,
)
from ..scrapers.iris import (
    IrisRaw,
)
from ..scrapers.iris import (
    detail_url as iris_detail_url,
)
from ..scrapers.iris import (
    fetch_listings as fetch_iris,
)
from ..scrapers.nipa import NipaRaw
from ..scrapers.nipa import detail_url as nipa_detail_url
from ..scrapers.nipa import fetch_listings as fetch_nipa
from ..scrapers.ntis import (
    NtisRaw,
)
from ..scrapers.ntis import (
    detail_url as ntis_detail_url,
)
from ..scrapers.ntis import (
    fetch_listings as fetch_ntis,
)
from . import register

logger = logging.getLogger(__name__)

SOURCE_KEY_BIZINFO = "bizinfo"
SOURCE_KEY_NIPA = "nipa"
SOURCE_KEY_IRIS = "iris"
SOURCE_KEY_NTIS = "ntis"
_FAR_FUTURE = date(9999, 12, 31)


@dataclass(frozen=True)
class _CachedSnapshot:
    items: list[Item]
    snapshot_date: date | None


def fetch() -> list[Item]:
    return fetch_result().items


@register("gov-support")
def fetch_result() -> AdapterResult:
    """Aggregate sources, then enrich NEW bizinfo items via DETAIL fetch,
    then sort by deadline. Caller is the scrape stage which feeds the result
    into diff + DB write."""
    items: list[Item] = []
    reports: list[SourceReport] = []
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
            bizinfo_fallback_items, fallback_report = _fallback_from_cache(
                SOURCE_KEY_BIZINFO, str(e),
            )
            reports.append(fallback_report)
        else:
            if bizinfo_result.complete:
                bizinfo_raws = bizinfo_result.items
                reports.append(SourceReport(
                    source_key=SOURCE_KEY_BIZINFO, status="fresh",
                    item_count=len(bizinfo_raws),
                ))
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
                error = f"incomplete pagination ({len(bizinfo_result.items)} items collected)"
                bizinfo_fallback_items, fallback_report = _fallback_from_cache(
                    SOURCE_KEY_BIZINFO, error,
                )
                reports.append(fallback_report)

        # --- 2. NIPA ---
        try:
            nipa_raws = fetch_nipa(client)
        except ScrapeError as e:
            logger.warning("nipa list failed: %s — falling back to last cached snapshot", e)
            nipa_raws = []
            nipa_fallback_items, fallback_report = _fallback_from_cache(
                SOURCE_KEY_NIPA, str(e),
            )
            reports.append(fallback_report)
        else:
            nipa_fallback_items = []
            reports.append(SourceReport(
                source_key=SOURCE_KEY_NIPA, status="fresh", item_count=len(nipa_raws),
            ))

        # --- 3. IRIS ---
        try:
            iris_result = fetch_iris(client)
        except ScrapeError as e:
            logger.warning("IRIS list failed: %s — falling back to cached snapshot", e)
            iris_raws = []
            iris_fallback_items, fallback_report = _fallback_from_cache(
                SOURCE_KEY_IRIS, str(e),
            )
            reports.append(fallback_report)
        else:
            if iris_result.complete:
                iris_raws = iris_result.items
                iris_fallback_items = []
                reports.append(SourceReport(
                    source_key=SOURCE_KEY_IRIS,
                    status="fresh",
                    item_count=len(iris_raws),
                ))
            else:
                iris_raws = []
                error = f"incomplete pagination ({len(iris_result.items)} items collected)"
                iris_fallback_items, fallback_report = _fallback_from_cache(
                    SOURCE_KEY_IRIS, error,
                )
                reports.append(fallback_report)

        # --- 4. NTIS: only upcoming/open announcements ---
        try:
            ntis_result = fetch_ntis(client)
        except ScrapeError as e:
            logger.warning("NTIS list failed: %s — falling back to cached snapshot", e)
            ntis_raws = []
            ntis_fallback_items, fallback_report = _fallback_from_cache(
                SOURCE_KEY_NTIS, str(e),
            )
            reports.append(fallback_report)
        else:
            if ntis_result.complete:
                ntis_raws = ntis_result.items
                ntis_fallback_items = []
                reports.append(SourceReport(
                    source_key=SOURCE_KEY_NTIS,
                    status="fresh",
                    item_count=len(ntis_raws),
                ))
            else:
                ntis_raws = []
                error = ntis_result.failure_reason or (
                    f"incomplete pagination ({len(ntis_result.items)} items collected)"
                )
                ntis_fallback_items, fallback_report = _fallback_from_cache(
                    SOURCE_KEY_NTIS, error,
                )
                reports.append(fallback_report)

        # --- 5. Identify which bizinfo stable_ids are NEW (need detail fetch).
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

        # --- 6. Fetch DETAIL for new bizinfo items (one HTTP call per item).
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

    # --- 7. Convert raws to Items
    items.extend(_from_bizinfo(r, details_by_id.get(r.pblanc_id)) for r in bizinfo_raws)
    items.extend(bizinfo_fallback_items)
    items.extend(_from_nipa(r) for r in nipa_raws)
    items.extend(nipa_fallback_items)
    items.extend(_from_iris(r) for r in iris_raws)
    items.extend(iris_fallback_items)
    items.extend(_from_ntis(r) for r in ntis_raws)
    items.extend(ntis_fallback_items)

    # --- 8. Sort by deadline asc, None last (stable for ties)
    items.sort(key=lambda i: i.deadline or _FAR_FUTURE)
    return AdapterResult(items=items, source_reports=tuple(reports))


def _load_cached_bizinfo_items() -> list[Item]:
    """Fallback for when the live bizinfo list fetch fails: return the last
    successfully-scraped bizinfo snapshot from the DB (whichever day that
    was — `last_seen` only advances on a day bizinfo actually succeeded, so
    this is stable across consecutive outage days). SAFE: closes its own
    connection."""
    return _load_cached_source_items(SOURCE_KEY_BIZINFO)


def _load_cached_source_items(source_key: str) -> list[Item]:
    """Compatibility wrapper for callers that only need cached items."""
    return _load_cached_source_snapshot(source_key).items


def _fallback_from_cache(
    source_key: str,
    error: str,
) -> tuple[list[Item], SourceReport]:
    snapshot = _load_cached_source_snapshot(source_key)
    return snapshot.items, SourceReport(
        source_key=source_key,
        status="fallback" if snapshot.items else "failed",
        item_count=len(snapshot.items),
        error=error,
        cached_snapshot_date=snapshot.snapshot_date,
    )


def _load_cached_source_snapshot(source_key: str) -> _CachedSnapshot:
    client = db.connect()
    try:
        db.migrate(client)
        result = client.execute(
            "SELECT i.stable_id, i.source_key, i.title, i.detail_url, i.category, "
            "i.organizer, i.amount, i.apply_period, i.deadline, i.region, i.target, "
            "i.summary, i.badges_json, i.first_seen, i.active_since, d.snapshot_date "
            "FROM daily_status d JOIN item i USING (stable_id) "
            "JOIN scrape_run r ON r.snapshot_date=d.snapshot_date "
            " AND r.archive_key=d.archive_key "
            "WHERE d.archive_key='gov-support' AND i.source_key=? "
            "AND d.status IN ('new','ongoing') AND r.status='complete' "
            "AND d.snapshot_date=("
            "  SELECT MAX(d2.snapshot_date) FROM daily_status d2 "
            "  JOIN item i2 USING (stable_id) "
            "  JOIN scrape_run r2 ON r2.snapshot_date=d2.snapshot_date "
            "   AND r2.archive_key=d2.archive_key "
            "  WHERE d2.archive_key='gov-support' AND i2.source_key=? "
            "   AND d2.status IN ('new','ongoing') AND r2.status='complete'"
            ")",
            (source_key, source_key),
        )
        if not result.rows:
            return _CachedSnapshot(items=[], snapshot_date=None)
        snapshot_date: date | None = None
        try:
            snapshot_date = date.fromisoformat(str(result.rows[0][-1]))
        except (TypeError, ValueError):
            logger.warning(
                "cached %s snapshot has invalid date: %r",
                source_key,
                result.rows[0][-1],
            )
        return _CachedSnapshot(
            items=[db._item_from_db_row(row[:-1]) for row in result.rows],
            snapshot_date=snapshot_date,
        )
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
    summary = (det.summary if det else None) or r.summary
    hashtags = list(dict.fromkeys([*r.hashtags, *(det.hashtags if det else ())]))
    region = (det.region if det else None) or None
    target = (det.target if det else None) or r.target
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


def _from_iris(r: IrisRaw) -> Item:
    return Item(
        stable_id=f"{SOURCE_KEY_IRIS}:{r.ancm_id}",
        source_key=SOURCE_KEY_IRIS,
        title=r.title,
        detail_url=iris_detail_url(r.ancm_id),
        category="R&D",
        organizer=r.organizer,
        apply_period=r.apply_period,
        deadline=r.deadline,
        badges=assign_badges(title=r.title, summary=None, organizer=r.organizer),
    )


def _from_ntis(r: NtisRaw) -> Item:
    return Item(
        stable_id=f"{SOURCE_KEY_NTIS}:{r.rnd_uid}",
        source_key=SOURCE_KEY_NTIS,
        title=r.title,
        detail_url=ntis_detail_url(r.rnd_uid),
        category="R&D",
        organizer=r.organizer,
        apply_period=r.apply_period,
        deadline=r.deadline,
        badges=assign_badges(title=r.title, summary=None, organizer=r.organizer),
    )
