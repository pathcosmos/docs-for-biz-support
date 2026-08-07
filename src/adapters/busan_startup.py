"""부산창업포털 실시간 공고와 장기 운영 서비스를 합치는 어댑터."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from ..models import AdapterResult, Item, SourceReport
from ..render.labels import assign_badges
from ..scrapers.base import HttpClient, ScrapeError
from ..scrapers.busan_startup import (
    BusanStartupRaw,
    detail_url,
    fetch_listings,
)
from . import register
from .kstartup_cache import load_cached_items

logger = logging.getLogger(__name__)

SOURCE_KEY_SUPPORT = "busan_startup"
SOURCE_KEY_SERVICE = "busan_service"
_DATA_FILE = Path(__file__).resolve().parent / "_data" / "busan_startup_static.json"

_CATEGORY_BY_CODE = {
    "1": "education",
    "2": "facility",
    "3": "mentoring",
    "4": "commercialization",
    "5": "policy_fund",
    "6": "rnd",
    "7": "global",
    "8": "networking",
}


def fetch() -> list[Item]:
    return fetch_result().items


@register("busan-startup")
def fetch_result() -> AdapterResult:
    reports: list[SourceReport] = []
    try:
        with HttpClient() as client:
            result = fetch_listings(client)
    except ScrapeError as exc:
        support_items = _load_support_fallback()
        reports.append(SourceReport(
            source_key=SOURCE_KEY_SUPPORT,
            status="fallback" if support_items else "failed",
            item_count=len(support_items),
            error=str(exc),
        ))
    else:
        if result.complete:
            support_items = [_from_raw(row) for row in result.items]
            reports.append(SourceReport(
                source_key=SOURCE_KEY_SUPPORT,
                status="fresh",
                item_count=len(support_items),
            ))
        else:
            support_items = _load_support_fallback()
            reports.append(SourceReport(
                source_key=SOURCE_KEY_SUPPORT,
                status="fallback" if support_items else "failed",
                item_count=len(support_items),
                error=f"incomplete pagination ({len(result.items)} items collected)",
            ))

    service_items = _load_static_items(SOURCE_KEY_SERVICE)
    reports.append(SourceReport(
        source_key=SOURCE_KEY_SERVICE,
        status="static",
        item_count=len(service_items),
    ))
    items = support_items + service_items
    items.sort(key=lambda item: (item.deadline is None, item.deadline or date.max, item.stable_id))
    return AdapterResult(items=items, source_reports=tuple(reports))


def _load_support_fallback() -> list[Item]:
    cached = load_cached_items("busan-startup", SOURCE_KEY_SUPPORT)
    return cached or _load_static_items(SOURCE_KEY_SUPPORT)


def _load_static_items(source_key: str) -> list[Item]:
    with _DATA_FILE.open(encoding="utf-8") as file:
        payload = json.load(file)
    return [_row_to_item(row) for row in payload["items"] if row["source_key"] == source_key]


def _from_raw(raw: BusanStartupRaw) -> Item:
    category = _CATEGORY_BY_CODE.get(raw.support_field_code or "")
    summary_parts = [value for value in (raw.support_field, raw.target) if value]
    return Item(
        stable_id=f"{SOURCE_KEY_SUPPORT}:{raw.business_code}",
        source_key=SOURCE_KEY_SUPPORT,
        title=raw.title,
        detail_url=detail_url(raw.business_code),
        category=category,
        organizer=raw.organizer,
        apply_period=raw.apply_period,
        deadline=raw.deadline,
        region="부산",
        target=raw.target,
        summary=" · ".join(summary_parts) or None,
        badges=assign_badges(title=raw.title, summary=None, organizer=raw.organizer),
    )


def _row_to_item(row: dict) -> Item:
    deadline_str = row.get("deadline")
    deadline = date.fromisoformat(deadline_str) if deadline_str else None
    badges_str = row.get("badges_json")
    badges: tuple[str, ...] = ()
    if badges_str:
        try:
            badges = tuple(json.loads(badges_str))
        except (TypeError, ValueError):
            badges = ()
    return Item(
        stable_id=row["stable_id"],
        source_key=row["source_key"],
        title=row["title"],
        detail_url=row["detail_url"],
        category=row.get("category"),
        organizer=row.get("organizer"),
        amount=row.get("amount"),
        apply_period=row.get("apply_period"),
        deadline=deadline,
        region=row.get("region"),
        target=row.get("target"),
        summary=row.get("summary"),
        badges=badges,
    )
