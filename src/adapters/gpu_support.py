"""GPU 지원사업 포털 수집 — gov-support 아카이브 안의 전용 섹션용 소스.

두 포털을 각각 독립적으로 수집한다:

- gpu_aiinfrahub : 국가 AI컴퓨팅자원 지원포털 (첨단 GPU 활용 지원 사업)
- gpu_aidc       : AI데이터센터(AIDC) 서비스 이용자 모집

별도 아카이브가 아니라 `gov-support` 어댑터가 호출하는 헬퍼다. 한 포털이
실패해도 다른 포털의 신선한 데이터를 버리지 않는다. 실패한 포털은 호출자가
넘긴 `fallback`(= gov-support의 소스별 마지막 complete 스냅샷 캐시)으로만
대체되고, 캐시도 없으면 "failed"로 보고되어 아카이브 전체가 실패 처리된다.

기업마당·NIPA 등 다른 소스의 항목은 건드리지 않는다 — 여기서는 추가로 GPU
포털 공고를 모아 전용 섹션(render/curation.SEC_GPU_PORTAL)에 보여 줄 뿐이다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from ..config.sources import GPU_PORTAL_SOURCES
from ..models import Item, SourceReport
from ..render.labels import GPU_AI_BADGE
from ..scrapers.base import HttpClient, ScrapeError
from ..scrapers.gpu_portals import (
    KST,
    GpuRaw,
    fetch_aidc,
    fetch_aiinfrahub,
    format_period,
    is_open,
)

logger = logging.getLogger(__name__)

SOURCE_KEY_AIINFRAHUB, SOURCE_KEY_AIDC = GPU_PORTAL_SOURCES

# aiinfrahub.kr은 중간 인증서를 보내지 않는다(리프만 전송). 공개 중간 CA를
# 저장소에 두고 이 사이트에만 추가 신뢰 앵커로 사용한다. 파일이 없으면 기본
# 신뢰 저장소만 쓰며(검증 실패 → 캐시 폴백), 검증을 끄지는 않는다.
_CERT_DIR = Path(__file__).resolve().parent.parent / "scrapers" / "certs"
_AIINFRAHUB_CA = _CERT_DIR / "globalsign-gcc-r6-alphassl-ca-2025.pem"

# (source_key, fetcher, organizer label, extra CA file)
_PORTALS: tuple[
    tuple[str, Callable[[HttpClient], list[GpuRaw]], str, Path | None], ...
] = (
    (SOURCE_KEY_AIINFRAHUB, fetch_aiinfrahub, "과기정통부·NIPA", _AIINFRAHUB_CA),
    (SOURCE_KEY_AIDC, fetch_aidc, "AI데이터센터(AIDC)", None),
)

# fallback(source_key, error) -> (cached items, SourceReport)
Fallback = Callable[[str, str], tuple[list[Item], SourceReport]]


def fetch_portals(
    fallback: Fallback, now: datetime | None = None,
) -> tuple[list[Item], list[SourceReport]]:
    now = now or datetime.now(UTC)
    out: list[Item] = []
    reports: list[SourceReport] = []
    seen: set[str] = set()
    for source_key, fetcher, organizer, ca_file in _PORTALS:
        extra_ca = ca_file if ca_file is not None and ca_file.is_file() else None
        with HttpClient(extra_ca_file=extra_ca) as client:
            try:
                raws = fetcher(client)
            except ScrapeError as e:
                logger.warning(
                    "%s failed: %s — falling back to last cached snapshot", source_key, e,
                )
                cached, report = fallback(source_key, str(e))
                reports.append(report)
                items = cached
            else:
                items = [
                    _to_item(source_key, organizer, r) for r in raws if is_open(r, now)
                ]
                reports.append(SourceReport(
                    source_key=source_key, status="fresh", item_count=len(items),
                ))
        for it in items:
            if it.stable_id not in seen:
                seen.add(it.stable_id)
                out.append(it)
    return out, reports


def _to_item(source_key: str, organizer: str, r: GpuRaw) -> Item:
    summary = r.summary
    if r.program:
        summary = f"{r.program} — {summary}" if summary else r.program
    return Item(
        stable_id=f"{source_key}:{r.site_id}",
        source_key=source_key,
        title=r.title,
        detail_url=r.detail_url,
        category=None,
        organizer=organizer,
        apply_period=format_period(r),
        deadline=r.end.astimezone(KST).date() if r.end else None,
        target=None,
        summary=summary,
        badges=(GPU_AI_BADGE,),
    )


__all__ = ["fetch_portals"]
