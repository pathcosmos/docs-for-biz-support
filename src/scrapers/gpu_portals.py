"""GPU 지원사업 공고 스크래퍼 — 두 공개 JSON API를 사용한다.

1. 국가 AI컴퓨팅자원 지원포털 (aiinfrahub.kr, 「첨단 GPU 활용 지원 사업」)
   GET /api/projects?searchType=projectName&searchText=&listSize=N&idx=P
   → {"totalCount": N, "projectDataList": [{id, title, projectName,
      startDate, endDate, content(HTML), ...}]}   (idx = 0-based page index)
   상세: https://aiinfrahub.kr/project/<id>

2. AI데이터센터(AIDC) 서비스 운영관리 시스템 (aidc.atops.or.kr)
   GET /api/v1/csp/announcement/meta?page=P&size=N&sort=startDate,desc  (P는 1-based)
   → {"content": [{announcementId, title, startDate, endDate, status,
      state, content(HTML), ...}], "page": {"totalPage": N, ...}}
   상세: https://aidc.atops.or.kr/announcements/<id>

두 사이트 모두 마감된 공고까지 목록에 남겨 두므로, 스크래퍼는 전체를 파싱해
돌려주고 "접수중" 여부 판단(`is_open`)은 어댑터가 한다. 응답 구조가 예상과
다르면 빈 목록으로 취급하지 않고 ScrapeError를 낸다 — 구조 변경을 "공고 없음"
으로 오인하면 캐시 폴백이 작동하지 않아 거짓 종료가 발생하기 때문이다.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from html import unescape

from .base import HttpClient, ScrapeError

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

AIINFRAHUB_BASE = "https://aiinfrahub.kr"
AIDC_BASE = "https://aidc.atops.or.kr"

PAGE_SIZE = 50
MAX_PAGES = 20  # 안전장치: 무한 루프 방지


@dataclass(frozen=True)
class GpuRaw:
    site_id: str
    title: str
    detail_url: str
    start: datetime | None      # tz-aware
    end: datetime | None        # tz-aware
    program: str | None         # 사업명 (aiinfrahub만 제공)
    summary: str | None


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def html_to_text(html: str | None, limit: int = 220) -> str | None:
    """공고 본문 HTML → 한 줄 요약. 비어 있으면 None."""
    if not html:
        return None
    text = unescape(_TAG_RE.sub(" ", html)).replace("\xa0", " ")
    text = _WS_RE.sub(" ", text).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _parse_dt(value: object) -> datetime | None:
    """ISO-8601 문자열 → tz-aware datetime. 타임존이 없으면 KST로 간주한다
    (AIDC는 '2026-11-13T09:00:00' 형태의 KST 벽시계 시각을 준다)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=KST)


def _loads(text: str, where: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ScrapeError(f"{where}: response is not JSON ({e})") from e
    if not isinstance(data, dict):
        raise ScrapeError(f"{where}: unexpected JSON root {type(data).__name__}")
    return data


def parse_aiinfrahub(data: dict) -> list[GpuRaw]:
    rows = data.get("projectDataList")
    if not isinstance(rows, list):
        raise ScrapeError("aiinfrahub: list structure was not recognized (projectDataList)")
    out: list[GpuRaw] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid, title = row.get("id"), row.get("title")
        if pid in (None, "") or not title:
            continue
        out.append(GpuRaw(
            site_id=str(pid),
            title=" ".join(str(title).split()),
            detail_url=f"{AIINFRAHUB_BASE}/project/{pid}",
            start=_parse_dt(row.get("startDate")),
            end=_parse_dt(row.get("endDate")),
            program=(str(row["projectName"]).strip() or None) if row.get("projectName") else None,
            summary=html_to_text(row.get("content")),
        ))
    return out


def fetch_aiinfrahub(client: HttpClient) -> list[GpuRaw]:
    out: list[GpuRaw] = []
    seen: set[str] = set()
    total: int | None = None
    for idx in range(MAX_PAGES):
        text = client.get(
            f"{AIINFRAHUB_BASE}/api/projects",
            params={"searchType": "projectName", "searchText": "",
                    "listSize": PAGE_SIZE, "idx": idx},
        )
        data = _loads(text, "aiinfrahub")
        page = parse_aiinfrahub(data)
        total_count = data.get("totalCount")
        if isinstance(total_count, int):
            total = total_count
        fresh = [r for r in page if r.site_id not in seen]
        for r in fresh:
            seen.add(r.site_id)
        out.extend(fresh)
        if not page or not fresh or total is None or len(out) >= total:
            break
    if total is not None and len(out) < total:
        raise ScrapeError(
            f"aiinfrahub: incomplete pagination ({len(out)} items collected of {total})"
        )
    return out


def parse_aidc(data: dict) -> list[GpuRaw]:
    rows = data.get("content")
    if not isinstance(rows, list):
        raise ScrapeError("aidc: list structure was not recognized (content)")
    out: list[GpuRaw] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        aid, title = row.get("announcementId"), row.get("title")
        if aid in (None, "") or not title:
            continue
        out.append(GpuRaw(
            site_id=str(aid),
            title=" ".join(str(title).split()),
            detail_url=f"{AIDC_BASE}/announcements/{aid}",
            start=_parse_dt(row.get("startDate")),
            end=_parse_dt(row.get("endDate")),
            program=None,
            summary=html_to_text(row.get("content")),
        ))
    return out


def fetch_aidc(client: HttpClient) -> list[GpuRaw]:
    """AIDC의 `page`는 사실상 1-based다: page=0과 page=1이 같은 첫 페이지를
    돌려준다(2026-11 확인). 1부터 요청하고, 수집 건수가 서버가 알려 준
    `totalElements`에 못 미치면 부분 수집으로 보고 ScrapeError를 낸다."""
    out: list[GpuRaw] = []
    seen: set[str] = set()
    total_pages = 1
    total_elements: int | None = None
    page_no = 1
    while page_no <= min(total_pages, MAX_PAGES):
        text = client.get(
            f"{AIDC_BASE}/api/v1/csp/announcement/meta",
            params={"page": page_no, "size": PAGE_SIZE, "sort": "startDate,desc"},
        )
        data = _loads(text, "aidc")
        page = parse_aidc(data)
        meta = data.get("page")
        if isinstance(meta, dict):
            if isinstance(meta.get("totalPage"), int):
                total_pages = max(1, meta["totalPage"])
            if isinstance(meta.get("totalElements"), int):
                total_elements = meta["totalElements"]
        fresh = [r for r in page if r.site_id not in seen]
        for r in fresh:
            seen.add(r.site_id)
        out.extend(fresh)
        if not fresh:
            break
        page_no += 1
    if total_elements is not None and len(out) < total_elements:
        raise ScrapeError(
            f"aidc: incomplete pagination ({len(out)} items collected of {total_elements})"
        )
    return out


def is_open(raw: GpuRaw, now: datetime | None = None) -> bool:
    """접수 마감 시각 전이면 True. 마감일을 알 수 없으면 (상시 모집으로 보고)
    True. 아직 시작 전인 예고 공고도 포함한다 — 미리 알리는 것이 목적."""
    now = now or datetime.now(UTC)
    if raw.end is None:
        return True
    return raw.end >= now


def format_period(raw: GpuRaw) -> str | None:
    if raw.start is None and raw.end is None:
        return None
    fmt = "%Y.%m.%d"
    s = raw.start.astimezone(KST).strftime(fmt) if raw.start else "?"
    e = raw.end.astimezone(KST).strftime(fmt) if raw.end else "?"
    return f"{s} ~ {e}"
