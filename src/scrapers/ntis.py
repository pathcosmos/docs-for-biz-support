"""NTIS 국가R&D 사업공고 중 접수예정·접수중 목록 수집기."""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import date

from bs4 import BeautifulSoup, Tag

from .base import HttpClient, ScrapeError

logger = logging.getLogger(__name__)

BASE = "https://www.ntis.go.kr"
LIST_PATH = "/rndgate/eg/un/ra/mng.do"
DETAIL_PATH = "/rndgate/eg/un/ra/view.do"
ACTIVE_STATUS_FILTER = "P,B"
PAGE_SIZE = 10
MAX_PAGES = 100

_ID_RE = re.compile(r"roRndUid=(\d+)")
_DATE_RE = re.compile(r"(\d{4})[./-](\d{2})[./-](\d{2})")


@dataclass(frozen=True)
class NtisRaw:
    rnd_uid: str
    title: str
    organizer: str | None
    status: str | None
    apply_period: str | None
    deadline: date | None


@dataclass(frozen=True)
class NtisListResult:
    items: list[NtisRaw]
    complete: bool


def fetch_listings(client: HttpClient) -> NtisListResult:
    """전체 이력 대신 현재 접수 가능한 상태만 페이지 단위로 수집한다."""
    url = f"{BASE}{LIST_PATH}"
    out: list[NtisRaw] = []
    seen: set[str] = set()
    total_count: int | None = None

    for page in range(1, MAX_PAGES + 1):
        try:
            html = client.get(url, params={
                "searchStatusList": ACTIVE_STATUS_FILTER,
                "pageIndex": page,
            })
        except ScrapeError as exc:
            if not out:
                raise
            logger.warning("NTIS page %d failed after %d items: %s", page, len(out), exc)
            return NtisListResult(items=out, complete=False)

        rows, parsed_total, valid_page = _parse_list_page(html)
        if not valid_page:
            if not out:
                raise ScrapeError("NTIS: list page structure was not recognized")
            return NtisListResult(items=out, complete=False)

        if total_count is None:
            total_count = parsed_total
        elif parsed_total != total_count:
            logger.warning(
                "NTIS result count changed during pagination (%d -> %d)",
                total_count,
                parsed_total,
            )
            return NtisListResult(items=out, complete=False)

        for row in rows:
            if row.rnd_uid not in seen:
                seen.add(row.rnd_uid)
                out.append(row)

        total_pages = max(1, math.ceil(total_count / PAGE_SIZE))
        if page >= total_pages:
            return NtisListResult(items=out, complete=True)
        if not rows:
            return NtisListResult(items=out, complete=False)

    logger.warning("NTIS hit MAX_PAGES=%d", MAX_PAGES)
    return NtisListResult(items=out, complete=False)


def _parse_list_page(html: str) -> tuple[list[NtisRaw], int, bool]:
    soup = BeautifulSoup(html, "lxml")
    total_input = soup.select_one("input#totalCount")
    table = soup.select_one("table")
    if not isinstance(total_input, Tag):
        return [], 0, False
    try:
        total_count = int(str(total_input.get("value", "")).replace(",", ""))
    except ValueError:
        return [], 0, False

    # NTIS 페이지에는 표가 여러 개 있으므로 roRndUid 링크가 있는 결과 표를 찾는다.
    for candidate in soup.find_all("table"):
        if isinstance(candidate, Tag) and candidate.find("a", href=_ID_RE):
            table = candidate
            break
    if total_count > 0 and not isinstance(table, Tag):
        return [], 0, False

    out: list[NtisRaw] = []
    if isinstance(table, Tag):
        for tr in table.select("tbody > tr"):
            anchor = tr.find("a", href=_ID_RE)
            if not isinstance(anchor, Tag):
                continue
            href = anchor.get("href")
            match = _ID_RE.search(href if isinstance(href, str) else "")
            if not match:
                continue
            title = anchor.get("title") or anchor.get_text(" ", strip=True)
            title = str(title).strip()
            if not title:
                continue

            status = _cell_text(tr, "현황")
            organizer = _cell_text(tr, "부처명")
            start_text = _cell_text(tr, "접수일")
            end_text = _cell_text(tr, "마감일")
            period = " ~ ".join(v for v in (start_text, end_text) if v) or None
            out.append(NtisRaw(
                rnd_uid=match.group(1),
                title=title,
                organizer=organizer,
                status=status,
                apply_period=period,
                deadline=_parse_date(end_text),
            ))

    return out, total_count, True


def _cell_text(row: Tag, data_title: str) -> str | None:
    cell = row.find("td", attrs={"data-title": data_title})
    if not isinstance(cell, Tag):
        return None
    return cell.get_text(" ", strip=True) or None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    match = _DATE_RE.search(value)
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def detail_url(rnd_uid: str) -> str:
    return f"{BASE}{DETAIL_PATH}?roRndUid={rnd_uid}"
