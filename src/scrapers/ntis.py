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
_ONCLICK_ID_RE = re.compile(r"fn_view\(\s*['\"](\d+)['\"]")
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
        if not rows and total_count > len(out):
            return NtisListResult(items=out, complete=False)
        if page >= total_pages:
            complete = len(out) == total_count
            if not complete:
                logger.warning(
                    "NTIS pagination count mismatch (expected %d, collected %d)",
                    total_count,
                    len(out),
                )
            return NtisListResult(items=out, complete=complete)

    logger.warning("NTIS hit MAX_PAGES=%d", MAX_PAGES)
    return NtisListResult(items=out, complete=False)


def _parse_list_page(html: str) -> tuple[list[NtisRaw], int, bool]:
    soup = BeautifulSoup(html, "lxml")
    total_input = soup.select_one("input#totalCount")
    if not isinstance(total_input, Tag):
        return [], 0, False
    try:
        total_count = int(str(total_input.get("value", "")).replace(",", ""))
    except ValueError:
        return [], 0, False

    # NTIS 페이지에는 표가 여러 개 있다. 2026-08부터 상세 링크의 roRndUid
    # 쿼리스트링이 사라지고 checkbox value / fn_view onclick으로 이동했으므로
    # 구형 링크와 신형 체크박스를 모두 결과 표 식별자로 인정한다.
    table: Tag | None = None
    for candidate in soup.find_all("table"):
        if not isinstance(candidate, Tag):
            continue
        has_legacy_id = candidate.find("a", href=_ID_RE)
        has_current_id = candidate.select_one("tbody input[name='selectCheckList']")
        if has_legacy_id or has_current_id:
            table = candidate
            break
    if total_count > 0 and not isinstance(table, Tag):
        return [], 0, False

    out: list[NtisRaw] = []
    if isinstance(table, Tag):
        for tr in table.select("tbody > tr"):
            anchor = tr.select_one("td[data-title='공고명'] a")
            if not isinstance(anchor, Tag):
                anchor = tr.find("a", href=_ID_RE)
            if not isinstance(anchor, Tag):
                continue
            rnd_uid = _row_id(tr, anchor)
            if not rnd_uid:
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
                rnd_uid=rnd_uid,
                title=title,
                organizer=organizer,
                status=status,
                apply_period=period,
                deadline=_parse_date(end_text),
            ))

    return out, total_count, True


def _row_id(row: Tag, anchor: Tag) -> str | None:
    """Extract an NTIS announcement id from current and legacy list markup."""
    href = anchor.get("href")
    legacy_match = _ID_RE.search(href if isinstance(href, str) else "")
    if legacy_match:
        return legacy_match.group(1)

    checkbox = row.select_one("input[name='selectCheckList']")
    if isinstance(checkbox, Tag):
        value = str(checkbox.get("value", "")).strip()
        if value.isdigit():
            return value

    onclick = anchor.get("onclick")
    onclick_match = _ONCLICK_ID_RE.search(onclick if isinstance(onclick, str) else "")
    return onclick_match.group(1) if onclick_match else None


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
