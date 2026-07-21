"""Adapter for the kstartup-mentoring archive — merges two K-Startup endpoints:

- webRND.do      → 🔬 R&D 카테고리
- webMNT_CNS.do  → 🧭 멘토링/컨설팅 카테고리

Each item's category is set by the source endpoint, so the renderer's
grouping logic gets correct buckets from day 1. Existing items keep their
prior category thanks to the COALESCE in db.py's UPSERT.

Each endpoint is fetched (and, on failure, falls back to cache) independently
— a broken webRND.do must not discard a successfully-scraped webMNT_CNS.do,
and vice versa. See kstartup_cache.py for the fallback rationale.
"""

from __future__ import annotations

import logging

from . import register
from .kstartup_cache import load_cached_items
from ..models import Item
from ..scrapers.base import HttpClient, ScrapeError
from ..scrapers.kstartup import KStartupRaw, detail_url, fetch_listings


logger = logging.getLogger(__name__)

SOURCE_KEY = "kstartup_mentoring"
ARCHIVE_KEY = "kstartup-mentoring"
# Order matters: same site_id appearing in both endpoints (rare) would resolve
# to the first occurrence; rnd wins because R&D listings are the more specific
# classification.
ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("webRND.do",     "rnd"),
    ("webMNT_CNS.do", "mentoring"),
)


@register("kstartup-mentoring")
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
                    "kstartup-mentoring %s failed: %s — falling back to last "
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
