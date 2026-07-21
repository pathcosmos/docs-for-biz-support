"""K-Startup 창업지원포털 list-page scraper.

Five endpoints share the same SSR HTML shape (`<ul class="gallery_list">` with
`<li>` rows whose `<a onclick="fn_goView('ID')">` carries the item id):

| endpoint            | menu label        | archive feed     | default category |
|---------------------|-------------------|------------------|------------------|
| webCMRCZN.do        | 사업화            | kstartup-biz     | (none)           |
| webRND.do           | R&D               | kstartup-mentoring | rnd            |
| webMNT_CNS.do       | 멘토링·컨설팅      | kstartup-mentoring | mentoring      |
| webFC_SP_NR.do      | 시설·공간·보육     | kstartup-global  | facility         |
| webGLOBAL.do        | 글로벌            | kstartup-global  | global           |

The list page itself returns each item's title and a short summary. Organizer
is uniformly '창업진흥원' for K-Startup listings, so we don't fetch detail
pages (which doubles the HTTP cost and adds 5xx risk).

Pagination: `?page=N`, ~20 items per page (varies). We iterate until a page
returns no new ids, or MAX_PAGES is reached.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

from .base import HttpClient, ScrapeError


logger = logging.getLogger(__name__)

K_STARTUP_BASE = "https://www.k-startup.go.kr/web/contents"
MAX_PAGES = 50  # hard stop in case pagination loops; each archive is well under 1000 items

# Shorter than HttpClient's default 20s — a multi-page walk hitting a hung
# connection (same failure shape observed for bizinfo: the request never
# gets a fast rejection, it just times out) wastes 3x the full timeout per
# page before giving up. See src/scrapers/bizinfo.py's LIST_PAGE_TIMEOUT for
# the fuller rationale from that investigation.
LIST_PAGE_TIMEOUT = 10.0


@dataclass(frozen=True)
class KStartupRaw:
    """Minimal record from the list page. Conversion to a full `Item` happens
    in the adapter (which knows the archive_key, default category, etc.)."""
    site_id: str       # e.g. '171020', used to build stable_id and detail_url
    title: str
    summary: str | None


@dataclass(frozen=True)
class KStartupListResult:
    items: list[KStartupRaw]
    complete: bool   # False if pagination stopped early due to a page failure


VALID_ENDPOINTS = frozenset({
    "webCMRCZN.do", "webRND.do", "webMNT_CNS.do", "webFC_SP_NR.do", "webGLOBAL.do",
})


def fetch_listings(
    client: HttpClient,
    endpoint: str,
) -> KStartupListResult:
    """Walk every page of an endpoint and return the merged list of raw items.

    A page that fails after retries stops pagination early rather than
    discarding everything gathered so far — the result is marked
    `complete=False` so the caller knows not to trust it as "today's full
    list" (see kstartup_cache.py's docstring for why that distinction
    matters). Raises ScrapeError only when literally nothing was collected."""
    if endpoint not in VALID_ENDPOINTS:
        raise ValueError(
            f"unknown K-Startup endpoint: {endpoint!r}. "
            f"Valid: {sorted(VALID_ENDPOINTS)}"
        )

    url = f"{K_STARTUP_BASE}/{endpoint}"
    out: list[KStartupRaw] = []
    seen_ids: set[str] = set()

    for page in range(1, MAX_PAGES + 1):
        try:
            html = client.get(url, params={"page": page}, timeout=LIST_PAGE_TIMEOUT)
        except ScrapeError as e:
            logger.warning(
                "kstartup %s page %d failed, stopping pagination early (%d "
                "items already collected this run): %s", endpoint, page, len(out), e,
            )
            if not out:
                raise
            return KStartupListResult(items=out, complete=False)

        page_items = _parse_list_page(html)
        if not page_items:
            logger.debug("kstartup %s page %d empty — stopping", endpoint, page)
            break
        # Defensive: detect a server quirk where pagination silently loops
        # the same items. If every id on this page was already seen, bail.
        new_on_page = [r for r in page_items if r.site_id not in seen_ids]
        if not new_on_page:
            logger.warning(
                "kstartup %s page %d returned only already-seen ids — stopping",
                endpoint, page,
            )
            break
        for r in new_on_page:
            seen_ids.add(r.site_id)
        out.extend(new_on_page)
    else:
        # for-else: MAX_PAGES exhausted without a break — log but accept what we have
        logger.warning("kstartup %s hit MAX_PAGES=%d", endpoint, MAX_PAGES)

    if not out:
        raise ScrapeError(f"k-startup {endpoint}: 0 items across all pages")
    return KStartupListResult(items=out, complete=True)


_ID_RE = re.compile(r"fn_goView\('(\d+)'\)")


def _parse_list_page(html: str) -> list[KStartupRaw]:
    """Pull `<li>` cards out of the page's `<ul class='gallery_list'>`.

    Each card structure (verified 2026-05-18):
        <li>
          <a href="#none" onclick="fn_goView('171020')">
            <div class="thumb">…</div>
            <div class="gallery_info">
              <div class="txt_sec">
                <div class="gallery_tit">TITLE</div>
              </div>
              <div class="date_sec">
                <div class="sub_tit">SUMMARY</div>
              </div>
            </div>
          </a>
        </li>
    """
    soup = BeautifulSoup(html, "lxml")
    ul = soup.find("ul", class_="gallery_list")
    if not isinstance(ul, Tag):
        return []

    out: list[KStartupRaw] = []
    for li in ul.find_all("li", recursive=False):
        if not isinstance(li, Tag):
            continue
        anchor = li.find("a")
        if not isinstance(anchor, Tag):
            continue
        onclick = anchor.get("onclick", "")
        m = _ID_RE.search(onclick if isinstance(onclick, str) else "")
        if not m:
            continue
        site_id = m.group(1)

        tit_div = li.find("div", class_="gallery_tit")
        title = tit_div.get_text(strip=True) if isinstance(tit_div, Tag) else ""
        if not title:
            continue

        sub_div = li.find("div", class_="sub_tit")
        summary = sub_div.get_text(strip=True) if isinstance(sub_div, Tag) else None

        out.append(KStartupRaw(site_id=site_id, title=title, summary=summary))
    return out


def detail_url(endpoint: str, site_id: str) -> str:
    """Reconstruct the public detail URL for an item. Used by adapters when
    building Item.detail_url."""
    return f"{K_STARTUP_BASE}/{endpoint}?schM=view&id={site_id}"
