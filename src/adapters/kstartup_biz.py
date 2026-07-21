"""Adapter for the kstartup-biz archive. Wraps the K-Startup webCMRCZN.do
list scraper and converts each raw record into an `Item`."""

from __future__ import annotations

import logging

from . import register
from .kstartup_cache import load_cached_items
from ..models import Item
from ..scrapers.base import HttpClient, ScrapeError
from ..scrapers.kstartup import KStartupRaw, detail_url, fetch_listings


logger = logging.getLogger(__name__)

ENDPOINT = "webCMRCZN.do"
SOURCE_KEY = "kstartup_biz"
ARCHIVE_KEY = "kstartup-biz"


@register("kstartup-biz")
def fetch() -> list[Item]:
    with HttpClient() as client:
        try:
            result = fetch_listings(client, ENDPOINT)
        except ScrapeError as e:
            logger.warning(
                "kstartup-biz list failed: %s — falling back to last cached snapshot", e,
            )
            return load_cached_items(ARCHIVE_KEY, SOURCE_KEY)

        if not result.complete:
            logger.warning(
                "kstartup-biz list incomplete (%d items collected) — falling "
                "back to last cached snapshot instead of a partial scrape",
                len(result.items),
            )
            return load_cached_items(ARCHIVE_KEY, SOURCE_KEY)

        raws = result.items
    return [_to_item(r) for r in raws]


def _to_item(r: KStartupRaw) -> Item:
    return Item(
        stable_id=f"{SOURCE_KEY}:{r.site_id}",
        source_key=SOURCE_KEY,
        title=r.title,
        detail_url=detail_url(ENDPOINT, r.site_id),
        category=None,                  # kstartup-biz has no category grouping
        organizer="창업진흥원",          # uniform for all K-Startup business listings
        summary=r.summary,
    )
