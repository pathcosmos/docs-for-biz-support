"""Adapter for the kstartup-biz archive. Wraps the K-Startup webCMRCZN.do
list scraper and converts each raw record into an `Item`."""

from __future__ import annotations

from . import register
from ..models import Item
from ..scrapers.base import HttpClient
from ..scrapers.kstartup import KStartupRaw, detail_url, fetch_listings


ENDPOINT = "webCMRCZN.do"
SOURCE_KEY = "kstartup_biz"


@register("kstartup-biz")
def fetch() -> list[Item]:
    with HttpClient() as client:
        raws = fetch_listings(client, ENDPOINT)
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
