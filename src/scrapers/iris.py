"""IRIS 범부처통합연구지원시스템 사업공고 목록 수집기."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

from bs4 import BeautifulSoup, Tag

from .base import HttpClient, ScrapeError

logger = logging.getLogger(__name__)

BASE = "https://www.iris.go.kr"
LIST_PATH = "/contents/retrieveBsnsAncmListView.do"
DETAIL_PATH = "/contents/retrieveBsnsAncmBtinSituDetailView.do"
MAX_PAGES = 50

_VIEW_RE = re.compile(
    r"f_bsnsAncmListForm_view\(\s*'(?P<ancm_id>[^']+)'\s*,"
    r"\s*'(?P<year>[^']*)'\s*,\s*'(?P<business_code>[^']*)'\s*,"
    r"\s*'(?P<sequence>[^']*)'"
)
_PAGE_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
_DATE_RE = re.compile(r"(\d{4})[./-](\d{2})[./-](\d{2})")


@dataclass(frozen=True)
class IrisRaw:
    ancm_id: str
    title: str
    organizer: str | None
    apply_period: str | None
    deadline: date | None


@dataclass(frozen=True)
class IrisListResult:
    items: list[IrisRaw]
    complete: bool


def fetch_listings(client: HttpClient) -> IrisListResult:
    """현재 공개된 IRIS 접수 공고를 끝 페이지까지 수집한다."""
    url = f"{BASE}{LIST_PATH}"
    by_id: dict[str, IrisRaw] = {}
    total_pages: int | None = None

    for page in range(1, MAX_PAGES + 1):
        try:
            html = client.get(url, params={"pageIndex": page})
        except ScrapeError as exc:
            if not by_id:
                raise
            logger.warning(
                "IRIS page %d failed after %d items: %s", page, len(by_id), exc
            )
            return IrisListResult(items=list(by_id.values()), complete=False)

        rows, parsed_total_pages, valid_page = _parse_list_page(html)
        if not valid_page:
            if not by_id:
                raise ScrapeError("IRIS: list page structure was not recognized")
            return IrisListResult(items=list(by_id.values()), complete=False)

        if total_pages is None:
            total_pages = parsed_total_pages
        elif parsed_total_pages != total_pages:
            logger.warning(
                "IRIS page count changed during pagination (%d -> %d)",
                total_pages,
                parsed_total_pages,
            )
            return IrisListResult(items=list(by_id.values()), complete=False)

        for row in rows:
            previous = by_id.get(row.ancm_id)
            if previous is None or _deadline_key(row) > _deadline_key(previous):
                by_id[row.ancm_id] = row

        if page >= total_pages:
            return IrisListResult(items=list(by_id.values()), complete=True)

    logger.warning("IRIS hit MAX_PAGES=%d", MAX_PAGES)
    return IrisListResult(items=list(by_id.values()), complete=False)


def _parse_list_page(html: str) -> tuple[list[IrisRaw], int, bool]:
    soup = BeautifulSoup(html, "lxml")
    container = soup.select_one(".tstyle.list.biz_announce ul.dbody")
    page_info = soup.select_one(".current_page")
    if not isinstance(container, Tag) or not isinstance(page_info, Tag):
        return [], 0, False

    page_match = _PAGE_RE.search(page_info.get_text(" ", strip=True))
    if not page_match:
        return [], 0, False
    total_pages = int(page_match.group(2))
    if total_pages < 1:
        return [], 0, False

    out: list[IrisRaw] = []
    for li in container.find_all("li", recursive=False):
        if not isinstance(li, Tag):
            continue
        anchor = li.select_one("strong.title a[onclick]")
        if not isinstance(anchor, Tag):
            continue
        onclick = anchor.get("onclick")
        match = _VIEW_RE.search(onclick if isinstance(onclick, str) else "")
        if not match:
            continue
        title = anchor.get_text(" ", strip=True)
        if not title:
            continue

        organizer_tag = li.select_one(".inst_title")
        organizer = (
            organizer_tag.get_text(" ", strip=True) if isinstance(organizer_tag, Tag) else None
        ) or None
        period_tag = li.select_one(".period")
        period = (
            period_tag.get_text(" ", strip=True) if isinstance(period_tag, Tag) else None
        ) or None
        out.append(IrisRaw(
            ancm_id=match.group("ancm_id"),
            title=title,
            organizer=organizer,
            apply_period=period,
            deadline=_parse_end_date(period),
        ))

    return out, total_pages, True


def _parse_end_date(period: str | None) -> date | None:
    if not period:
        return None
    matches = _DATE_RE.findall(period)
    if not matches:
        return None
    year, month, day = matches[-1]
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _deadline_key(item: IrisRaw) -> date:
    return item.deadline or date.min


def detail_url(ancm_id: str) -> str:
    return f"{BASE}{DETAIL_PATH}?ancmId={ancm_id}"
