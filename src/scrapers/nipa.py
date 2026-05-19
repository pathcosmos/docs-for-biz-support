"""NIPA 정보통신산업진흥원 — 사업소개 list scraper.

Endpoint: GET https://www.nipa.kr/home/bsnsAll/0/select

The list is a flat `<ul>` of `<li><a href="./detail?bsnsDtlsIemNo=N">title</a></li>`
items. ~24 programs visible at once (the whole catalog fits on page 1 —
pagination params seem to be ignored or return inconsistent subsets, so we
trust page 1).

Detail page (./detail?bsnsDtlsIemNo=N) holds period / organizer / hashtags,
but we keep the first iteration list-only — every NIPA program is AI/cloud
related so the GPU·AI badge fires from the title alone.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

from .base import HttpClient, ScrapeError


logger = logging.getLogger(__name__)

BASE = "https://www.nipa.kr"
LIST_PATH = "/home/bsnsAll/0/select"
DETAIL_PATH = "/home/bsnsAll/0/detail"


@dataclass(frozen=True)
class NipaRaw:
    bsns_id: str       # 'bsnsDtlsIemNo' value, e.g. '900'
    title: str


_ID_RE = re.compile(r"bsnsDtlsIemNo=(\d+)")


def fetch_listings(client: HttpClient) -> list[NipaRaw]:
    """Return all visible NIPA program entries from the list page."""
    html = client.get(f"{BASE}{LIST_PATH}")
    soup = BeautifulSoup(html, "lxml")

    out: list[NipaRaw] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=_ID_RE):
        if not isinstance(a, Tag):
            continue
        href = a.get("href") or ""
        if not isinstance(href, str):
            continue
        m = _ID_RE.search(href)
        if not m:
            continue
        bsns_id = m.group(1)
        if bsns_id in seen:
            continue
        title = a.get_text(strip=True)
        if not title:
            continue
        seen.add(bsns_id)
        out.append(NipaRaw(bsns_id=bsns_id, title=title))

    if not out:
        raise ScrapeError("nipa: list page returned 0 entries")
    return out


def detail_url(bsns_id: str) -> str:
    return f"{BASE}{DETAIL_PATH}?bsnsDtlsIemNo={bsns_id}"
