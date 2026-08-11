from datetime import date

from src.models import SourceReport


def test_source_report_summarizes_dual_runner_bizinfo_failure():
    report = SourceReport(
        source_key="bizinfo",
        status="fallback",
        item_count=1514,
        error=(
            "bizinfo API artifact unavailable after Ubuntu and macOS collection attempts: "
            "missing file"
        ),
        cached_snapshot_date=date(2026, 8, 10),
    )

    assert report.warning == (
        "bizinfo: 수집 실패 — 2026-08-10 스냅샷 1514건 사용 "
        "(Ubuntu·macOS API 수집 실패)"
    )
