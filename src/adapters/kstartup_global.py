"""Adapter for the kstartup-global archive — merges two K-Startup endpoints:

- webFC_SP_NR.do → 🏢 시설/공간 카테고리
- webGLOBAL.do   → 🌏 글로벌 카테고리

Each endpoint is fetched (and, on failure, falls back to cache) independently
— a broken webFC_SP_NR.do must not discard a successfully-scraped
webGLOBAL.do, and vice versa. See kstartup_cache.py for the fallback
rationale.
"""

from __future__ import annotations

import logging

from . import register
from .kstartup_cache import load_cached_items
from ..models import Item
from ..scrapers.base import HttpClient, ScrapeError
from ..scrapers.kstartup import KStartupRaw, detail_url, fetch_listings


logger = logging.getLogger(__name__)

SOURCE_KEY = "kstartup_global"
ARCHIVE_KEY = "kstartup-global"
ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("webFC_SP_NR.do", "facility"),
    ("webGLOBAL.do",   "global"),
)


@register("kstartup-global")
def fetch() -> list[Item]:
    seen: set[str] = set()
    out: list[Item] = []
    with HttpClient() as client:
        for endpoint, category in ENDPOINTS:
            try:
                result = fetch_listings(client, endpoint)
                incomplete_reason = None if result.complete else (
                    f"incomplete ({len(result.items)} items collected)"
                )
            except ScrapeError as e:
                result = None
                incomplete_reason = str(e)

            if result is None or not result.complete:
                logger.warning(
                    "kstartup-global %s failed: %s — falling back to last "
                    "cached snapshot for category=%s", endpoint, incomplete_reason, category,
                )
                cached = load_cached_items(ARCHIVE_KEY, SOURCE_KEY, category=category)
                for it in cached:
                    if it.stable_id in seen:
                        continue
                    seen.add(it.stable_id)
                    out.append(it)
                continue

            for r in result.items:
                stable_id = f"{SOURCE_KEY}:{r.site_id}"
                if stable_id in seen:
                    continue
                seen.add(stable_id)
                out.append(_to_item(r, endpoint, category))
    return out


def _to_item(r: KStartupRaw, endpoint: str, category: str) -> Item:
    return Item(
        stable_id=f"{SOURCE_KEY}:{r.site_id}",
        source_key=SOURCE_KEY,
        title=r.title,
        detail_url=detail_url(endpoint, r.site_id),
        category=category,
        organizer="창업진흥원",
        summary=r.summary,
    )
