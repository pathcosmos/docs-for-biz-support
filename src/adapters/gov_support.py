"""Adapter for the gov-support archive.

This archive is a multi-source aggregator. PR4b lands the first source
(bizinfo / 기업마당) which covers ~41% of items in the legacy archive.
smes / iris / ntis / smart-factory / 6 technoparks land in PR8.

Sorting: items are returned sorted by deadline ascending (closest first).
Items with no deadline (e.g. '예산 소진시까지') go LAST. This means the
mail's '진행 중' section opens with the most time-sensitive announcements,
which matters because the body is large and Gmail clips at ~102 KB — the
recipient sees the important things even when the body is truncated.
"""

from __future__ import annotations

from datetime import date

from . import register
from ..models import Item
from ..scrapers.base import HttpClient
from ..scrapers.bizinfo import BizinfoRaw, detail_url as bizinfo_detail_url, fetch_listings as fetch_bizinfo


SOURCE_KEY_BIZINFO = "bizinfo"


@register("gov-support")
def fetch() -> list[Item]:
    items: list[Item] = []
    with HttpClient() as client:
        bizinfo_raws = fetch_bizinfo(client)
    items.extend(_from_bizinfo(r) for r in bizinfo_raws)

    # Deadline ascending; None last. Stable sort preserves insertion order
    # for ties (so multiple items with the same deadline keep source order).
    _FAR_FUTURE = date(9999, 12, 31)
    items.sort(key=lambda i: i.deadline or _FAR_FUTURE)
    return items


def _from_bizinfo(r: BizinfoRaw) -> Item:
    """기업마당 row → Item. The bizinfo `소관부처·지자체` column maps to the
    legacy '🏢 주관기관' field; `사업수행기관` is dropped (the legacy archive
    didn't surface it as a distinct field). `지원분야` (내수/창업/경영/...) is
    a coarse category but doesn't fit the existing 'category' field semantics
    (gov-support is ungrouped), so we just leave it off."""
    return Item(
        stable_id=f"{SOURCE_KEY_BIZINFO}:{r.pblanc_id}",
        source_key=SOURCE_KEY_BIZINFO,
        title=r.title,
        detail_url=bizinfo_detail_url(r.pblanc_id),
        category=None,
        organizer=r.organizer,
        apply_period=r.apply_period,
        deadline=r.deadline,
        # bizinfo list page doesn't surface region/target/summary — those live
        # on the detail page. Leave as None for now; the renderer skips empty
        # rows so the item card stays clean.
    )
