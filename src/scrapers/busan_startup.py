"""부산창업포털의 현재 접수중 지원사업 JSON API 수집기."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date

from .base import HttpClient, ScrapeError

logger = logging.getLogger(__name__)

BASE = "https://www.busanstartup.kr"
LIST_PATH = "/_Api/bizListData"
DETAIL_PATH = "/biz_sup"
MAX_PAGES = 50

_BASE_PARAMS: dict[str, str | int] = {
    "pageType": "01",
    "deadline": "N",
    "s_orderby": "regi",
    "userYn": "Y",
    "mcode": "biz02",
    "s_desc": "desc",
    "s_busi_gubun": 0,
    "s_appl_type": 0,
}


@dataclass(frozen=True)
class BusanStartupRaw:
    business_code: str
    title: str
    support_field: str | None
    support_field_code: str | None
    organizer: str | None
    target: str | None
    apply_period: str | None
    deadline: date | None


@dataclass(frozen=True)
class BusanStartupListResult:
    items: list[BusanStartupRaw]
    complete: bool


def fetch_listings(client: HttpClient) -> BusanStartupListResult:
    """접수중 필터를 고정해 전체 페이지를 수집한다."""
    url = f"{BASE}{LIST_PATH}"
    out: list[BusanStartupRaw] = []
    seen: set[str] = set()
    total_count: int | None = None
    final_page: int | None = None

    for page in range(1, MAX_PAGES + 1):
        params = dict(_BASE_PARAMS, pageNo=page)
        try:
            text = client.get(url, params=params)
            rows, current_total, current_final = _parse_page(text)
        except ScrapeError as exc:
            if not out:
                raise
            logger.warning(
                "Busan Startup page %d failed after %d items: %s", page, len(out), exc
            )
            return BusanStartupListResult(items=out, complete=False)

        if total_count is None:
            total_count = current_total
            final_page = current_final
        elif current_total != total_count or current_final != final_page:
            logger.warning("Busan Startup result count changed during pagination")
            return BusanStartupListResult(items=out, complete=False)

        for row in rows:
            if row.business_code not in seen:
                seen.add(row.business_code)
                out.append(row)

        if page >= current_final:
            if len(out) != current_total:
                logger.warning(
                    "Busan Startup count mismatch: expected %d, parsed %d",
                    current_total,
                    len(out),
                )
                return BusanStartupListResult(items=out, complete=False)
            return BusanStartupListResult(items=out, complete=True)
        if not rows:
            return BusanStartupListResult(items=out, complete=False)

    logger.warning("Busan Startup hit MAX_PAGES=%d", MAX_PAGES)
    return BusanStartupListResult(items=out, complete=False)


def _parse_page(text: str) -> tuple[list[BusanStartupRaw], int, int]:
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ScrapeError(f"Busan Startup: invalid JSON response: {exc}") from exc

    page_info = payload.get("pageInfo")
    raw_rows = payload.get("list")
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("resultCode") != 200:
        raise ScrapeError("Busan Startup: API returned a non-success result")
    if not isinstance(page_info, dict) or not isinstance(raw_rows, list):
        raise ScrapeError("Busan Startup: missing list/pageInfo in response")
    try:
        total_count = int(page_info["totalCount"])
        final_page = max(1, int(page_info["finalPageNo"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ScrapeError("Busan Startup: invalid pagination metadata") from exc

    out: list[BusanStartupRaw] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        business_code = str(raw.get("busi_code") or "").strip()
        title = str(raw.get("busi_title") or "").strip()
        if not business_code or not title:
            continue
        always_open = raw.get("appl_dtype") == "Y"
        start = _text(raw.get("appl_sdate"))
        end = _text(raw.get("appl_edate"))
        apply_period = "상시" if always_open else " ~ ".join(v for v in (start, end) if v)
        out.append(BusanStartupRaw(
            business_code=business_code,
            title=title,
            support_field=_text(raw.get("busi_gubun")),
            support_field_code=_text(raw.get("busi_gubun_code")),
            organizer=_text(raw.get("busi_comp")),
            target=_text(raw.get("appl_type")),
            apply_period=apply_period or None,
            deadline=None if always_open else _parse_date(end),
        ))
    return out, total_count, final_page


def _text(value: object) -> str | None:
    if value is None:
        return None
    return str(value).strip() or None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def detail_url(business_code: str) -> str:
    return f"{BASE}{DETAIL_PATH}/{business_code}?mcode=biz02"
