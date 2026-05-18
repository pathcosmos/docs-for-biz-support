"""Adapter for the kstartup-global archive — merges two K-Startup endpoints:

- webFC_SP_NR.do → 🏢 시설/공간 카테고리
- webGLOBAL.do   → 🌏 글로벌 카테고리
"""

from __future__ import annotations

from . import register
from ..models import Item
from ..scrapers.base import HttpClient
from ..scrapers.kstartup import KStartupRaw, detail_url, fetch_listings


SOURCE_KEY = "kstartup_global"
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
