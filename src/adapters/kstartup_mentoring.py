"""Adapter for the kstartup-mentoring archive — merges two K-Startup endpoints:

- webRND.do      → 🔬 R&D 카테고리
- webMNT_CNS.do  → 🧭 멘토링/컨설팅 카테고리

Each item's category is set by the source endpoint, so the renderer's
grouping logic gets correct buckets from day 1. Existing items keep their
prior category thanks to the COALESCE in db.py's UPSERT.
"""

from __future__ import annotations

from . import register
from ..models import Item
from ..scrapers.base import HttpClient
from ..scrapers.kstartup import KStartupRaw, detail_url, fetch_listings


SOURCE_KEY = "kstartup_mentoring"
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
            raws = fetch_listings(client, endpoint)
            for r in raws:
                if r.site_id in seen:
                    continue
                seen.add(r.site_id)
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
