"""기업마당 (bizinfo.go.kr) list scraper.

List endpoint: GET https://www.bizinfo.go.kr/sii/siia/selectSIIA200View.do?cpage=N
Each page is server-rendered HTML with a single `<table>` inside
`<div class="table_Type_1">`. Each `<tr>` in `<tbody>` has 8 columns:

    번호 | 지원분야 | 사업명+링크 | 신청기간 | 소관부처·지자체 | 사업수행기관 | 등록일 | 조회수

The link in column 3 carries `pblancId=PBLN_NNNNNNNNNNNN` (12 zero-padded
digits) which is the stable identifier used in legacy archives.

Pagination: empty `<tbody>` at MAX_PAGES+1; we walk until a page yields 0 rows.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

from bs4 import BeautifulSoup, Tag

from .base import HttpClient, ScrapeError


logger = logging.getLogger(__name__)

BASE = "https://www.bizinfo.go.kr"
LIST_PATH = "/sii/siia/selectSIIA200View.do"
DETAIL_PATH = "/sii/siia/selectSIIA200Detail.do"
MAX_PAGES = 200    # 15/page × 200 = 3000 ceiling — plenty


@dataclass(frozen=True)
class BizinfoRaw:
    pblanc_id: str                  # 'PBLN_000000000122138'
    title: str
    apply_period: str | None
    deadline: date | None
    organizer: str | None           # 소관부처·지자체 (col 5)
    executor: str | None            # 사업수행기관 (col 6) — kept separate
    support_field: str | None       # 지원분야 (col 2) — e.g. '내수', '창업'


_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_PBLANC_RE = re.compile(r"pblancId=(PBLN_\d+)")


def fetch_listings(client: HttpClient) -> list[BizinfoRaw]:
    """Walk every page of the bizinfo list until empty, return merged items."""
    url = f"{BASE}{LIST_PATH}"
    out: list[BizinfoRaw] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        html = client.get(url, params={"cpage": page})
        rows = _parse_list_page(html)
        if not rows:
            logger.debug("bizinfo page %d empty — stopping", page)
            break
        # Defensive: if every id was already seen we're in a pagination loop
        new_rows = [r for r in rows if r.pblanc_id not in seen]
        if not new_rows:
            logger.warning("bizinfo page %d returned only seen ids — stopping", page)
            break
        for r in new_rows:
            seen.add(r.pblanc_id)
        out.extend(new_rows)
    else:
        logger.warning("bizinfo hit MAX_PAGES=%d", MAX_PAGES)

    if not out:
        raise ScrapeError("bizinfo: 0 items across all pages")
    return out


def _parse_list_page(html: str) -> list[BizinfoRaw]:
    soup = BeautifulSoup(html, "lxml")
    container = soup.find("div", class_="table_Type_1")
    if not isinstance(container, Tag):
        return []
    table = container.find("table")
    if not isinstance(table, Tag):
        return []
    tbody = table.find("tbody")
    if not isinstance(tbody, Tag):
        return []

    out: list[BizinfoRaw] = []
    for tr in tbody.find_all("tr", recursive=False):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 6:
            continue
        anchor = cells[2].find("a")
        if not isinstance(anchor, Tag):
            continue
        href = anchor.get("href", "")
        m = _PBLANC_RE.search(href if isinstance(href, str) else "")
        if not m:
            continue
        pblanc_id = m.group(1)
        title = anchor.get_text(strip=True)
        if not title:
            continue

        apply_period_raw = cells[3].get_text(" ", strip=True)
        apply_period = re.sub(r"\s+", " ", apply_period_raw).strip() or None
        deadline = _parse_end_date(apply_period) if apply_period else None

        support_field = cells[1].get_text(strip=True) or None
        organizer = cells[4].get_text(strip=True) or None
        executor = cells[5].get_text(strip=True) or None

        out.append(BizinfoRaw(
            pblanc_id=pblanc_id,
            title=title,
            apply_period=apply_period,
            deadline=deadline,
            organizer=organizer,
            executor=executor,
            support_field=support_field,
        ))
    return out


def _parse_end_date(period: str) -> date | None:
    """Pull the END date out of a '2026-05-15 ~ 2026-06-12' style range. If
    only one date is present, treat it as the deadline. Returns None on
    unparseable input."""
    dates = _DATE_RE.findall(period)
    if not dates:
        return None
    y, m, d = dates[-1]
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def detail_url(pblanc_id: str) -> str:
    """Canonical detail URL — matches the legacy archive's stored URLs."""
    return f"{BASE}{DETAIL_PATH}?pblancId={pblanc_id}"


@dataclass(frozen=True)
class BizinfoDetail:
    """Fields extracted from the bizinfo DETAIL page (beyond what the list
    page provides). All optional — empty strings/None when the page omits a
    section. Used by the adapter to enrich `Item` (set badges, summary)."""
    pblanc_id: str
    summary: str | None        # 사업개요 본문 (truncated)
    hashtags: tuple[str, ...]  # 해시태그 (#인공지능, #창업, …)
    region: str | None         # 지역 (해당 시도/지역)
    target: str | None         # 지원대상
    amount: str | None         # 지원금액


_HASHTAG_RE = re.compile(r"#[^\s<>#]{1,30}")
_OVERVIEW_HEADINGS = ("사업개요", "사업 개요", "개요")


def fetch_detail(client: HttpClient, pblanc_id: str) -> BizinfoDetail:
    """Fetch one DETAIL page and extract enrichment fields. Best-effort —
    missing sections silently return None/empty. Used for the FIRST sighting
    of a stable_id only (subsequent runs hit the DB cache via COALESCE)."""
    html = client.get(detail_url(pblanc_id))
    soup = BeautifulSoup(html, "lxml")

    # 1. 사업개요 — collect <p>/<div> text under any element whose text starts
    # with one of the overview headings. Fallback: the longest <div class="txt">.
    summary: str | None = None
    for el in soup.find_all(["dt", "dl", "th", "h3", "h4", "strong"]):
        if not isinstance(el, Tag):
            continue
        head_text = el.get_text(strip=True)
        if any(head_text.startswith(h) for h in _OVERVIEW_HEADINGS):
            # Search the next siblings / nearest <dd>/<div> for the body text
            sibling = el.find_next(["dd", "div", "p"])
            if isinstance(sibling, Tag):
                body = sibling.get_text(" ", strip=True)
                if body:
                    summary = body[:500]
                    break
    if not summary:
        # Fallback: longest div.txt with substantial Korean text
        candidates = [
            d for d in soup.find_all("div", class_="txt")
            if isinstance(d, Tag) and len(d.get_text(strip=True)) > 30
        ]
        if candidates:
            summary = max(
                (c.get_text(" ", strip=True) for c in candidates),
                key=len,
            )[:500]

    # 2. Hashtags — bizinfo detail pages render hashtags like `<a>#인공지능</a>`.
    # Search the entire body text (the list also catches anything in the
    # 'related categories' / 'tags' panel without being too greedy).
    page_text = soup.get_text(" ", strip=True)
    hashtags = tuple(sorted(set(_HASHTAG_RE.findall(page_text))))

    # 3. Optional 지역 / 지원대상 / 지원금액 — many detail pages use a label/value
    # <table>; pick them up best-effort.
    region: str | None = None
    target: str | None = None
    amount: str | None = None
    for row in soup.find_all("tr"):
        if not isinstance(row, Tag):
            continue
        cells = row.find_all(["th", "td"], limit=2)
        if len(cells) != 2:
            continue
        label = cells[0].get_text(strip=True)
        value = cells[1].get_text(" ", strip=True)
        if not value:
            continue
        if "지역" in label and region is None:
            region = value[:100]
        elif "지원대상" in label and target is None:
            target = value[:200]
        elif "지원금액" in label and amount is None:
            amount = value[:200]

    return BizinfoDetail(
        pblanc_id=pblanc_id,
        summary=summary,
        hashtags=hashtags,
        region=region,
        target=target,
        amount=amount,
    )
