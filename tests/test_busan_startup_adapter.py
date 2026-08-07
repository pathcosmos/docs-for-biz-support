from src.adapters import busan_startup
from src.scrapers.busan_startup import BusanStartupListResult, BusanStartupRaw


def _raw(code: str) -> BusanStartupRaw:
    return BusanStartupRaw(
        business_code=code,
        title="부산 AI 지원사업",
        support_field="시설·공간",
        support_field_code="2",
        organizer="부산기술창업투자원",
        target="전체창업자",
        apply_period="2026-08-01 ~ 2026-08-21",
        deadline=None,
    )


def test_live_support_replaces_frozen_support_but_keeps_static_services(monkeypatch):
    monkeypatch.setattr(
        busan_startup,
        "fetch_listings",
        lambda client: BusanStartupListResult(items=[_raw("2201")], complete=True),
    )

    result = busan_startup.fetch_result()

    support = [item for item in result.items if item.source_key == "busan_startup"]
    services = [item for item in result.items if item.source_key == "busan_service"]
    assert [item.stable_id for item in support] == ["busan_startup:2201"]
    assert len(services) == 12
    assert next(r for r in result.source_reports if r.source_key == "busan_startup").status == "fresh"


def test_incomplete_live_support_uses_fallback(monkeypatch):
    monkeypatch.setattr(
        busan_startup,
        "fetch_listings",
        lambda client: BusanStartupListResult(items=[_raw("partial")], complete=False),
    )
    fallback = busan_startup._from_raw(_raw("cached"))
    monkeypatch.setattr(busan_startup, "_load_support_fallback", lambda: [fallback])

    result = busan_startup.fetch_result()

    support = [item.stable_id for item in result.items if item.source_key == "busan_startup"]
    assert support == ["busan_startup:cached"]
    report = next(r for r in result.source_reports if r.source_key == "busan_startup")
    assert report.status == "fallback"
