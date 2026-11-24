"""GPU 지원사업 포털(aiinfrahub/AIDC): 스크래퍼 파싱·페이지네이션, 접수중 필터,
소스별 캐시 폴백, gov-support 통합과 전용 섹션 분류를 검증한다. 네트워크는
쓰지 않는다."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from src.adapters import gov_support, gpu_support
from src.config.archives import ARCHIVES
from src.config.sources import GPU_PORTAL_SOURCES, SOURCES
from src.models import Item, SourceReport
from src.render import curation
from src.render.curation import SEC_GPU_PORTAL, classify_for_curation
from src.render.labels import GPU_AI_BADGE
from src.scrapers import gpu_portals
from src.scrapers.base import HttpClient, ScrapeError
from src.scrapers.gpu_portals import (
    GpuRaw,
    fetch_aidc,
    fetch_aiinfrahub,
    format_period,
    html_to_text,
    is_open,
    parse_aidc,
    parse_aiinfrahub,
)

NOW = datetime(2026, 11, 23, 3, 0, tzinfo=UTC)   # 2026-11-23 12:00 KST


# ── fixtures mirroring the real API shapes (verified 2026-11-23) ────────────

def _hub_row(pid, title, start, end, name="「첨단 GPU 활용 지원 사업」", content="<p>본문&nbsp;내용</p>"):
    return {
        "id": pid, "projectType": "1", "projectName": name, "title": title,
        "startDate": start, "endDate": end, "content": content, "status": 1,
    }


HUB_OPEN = _hub_row("07", "2026년 베타서비스 참여자 모집 공고",
                    "2026-11-19T02:40:00.000+00:00", "2026-11-24T06:00:00.000+00:00")
HUB_CLOSED = _hub_row("06", "[접수마감] 이전 모집 공고",
                      "2026-10-23T06:00:00.000+00:00", "2026-10-28T06:00:00.000+00:00")


def _aidc_row(aid, title, start, end, state="IN_PROGRESS"):
    return {
        "announcementId": aid, "title": title, "startDate": start, "endDate": end,
        "status": "ACTIVE", "state": state, "content": "<p>AIDC <b>정기모집</b></p>",
    }


AIDC_OPEN = _aidc_row(532, "2027년 AI데이터센터 서비스 이용자 정기모집(1트랙 - 단일노드)",
                      "2026-11-13T09:00:00", "2026-12-04T14:00:00")
AIDC_CLOSED = _aidc_row(528, "2026년 정기모집(추가 2차) 공고",
                        "2026-09-11T13:30:00", "2026-09-22T14:00:00", state="FINISHED")


class _FakeClient:
    """Serves canned JSON pages keyed by the page index param."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, params=None, **_):
        self.calls.append((url, dict(params or {})))
        key = params.get("idx", params.get("page", 0)) if params else 0
        return json.dumps(self.pages[key])


# ── scraper: parsing ────────────────────────────────────────────────────────

def test_parse_aiinfrahub_maps_fields_and_detail_url():
    (raw,) = parse_aiinfrahub({"projectDataList": [HUB_OPEN]})
    assert raw.site_id == "07"
    assert raw.detail_url == "https://aiinfrahub.kr/project/07"
    assert raw.program == "「첨단 GPU 활용 지원 사업」"
    assert raw.summary == "본문 내용"
    assert raw.end == datetime(2026, 11, 24, 6, 0, tzinfo=UTC)


def test_parse_aidc_treats_naive_timestamps_as_kst():
    (raw,) = parse_aidc({"content": [AIDC_OPEN]})
    assert raw.site_id == "532"
    assert raw.detail_url == "https://aidc.atops.or.kr/announcements/532"
    assert raw.end == datetime(2026, 12, 4, 5, 0, tzinfo=UTC)   # 14:00 KST
    assert format_period(raw) == "2026.11.13 ~ 2026.12.04"


@pytest.mark.parametrize("parser,payload", [
    (parse_aiinfrahub, {"unexpected": []}),
    (parse_aidc, {"unexpected": []}),
])
def test_unrecognised_structure_raises_instead_of_empty(parser, payload):
    with pytest.raises(ScrapeError, match="not recognized"):
        parser(payload)


def test_non_json_response_raises():
    client = _FakeClient({0: {}})
    client.get = lambda url, params=None, **_: "<html>blocked</html>"
    with pytest.raises(ScrapeError, match="not JSON"):
        fetch_aiinfrahub(client)


def test_rows_without_id_or_title_are_skipped():
    rows = [{"id": "", "title": "x"}, {"id": "9", "title": ""}, HUB_OPEN]
    assert [r.site_id for r in parse_aiinfrahub({"projectDataList": rows})] == ["07"]


def test_html_to_text_strips_tags_entities_and_truncates():
    assert html_to_text("<p>a&nbsp;<b>b</b></p>\n<p>c</p>") == "a b c"
    assert html_to_text("<p> </p>") is None
    assert html_to_text(None) is None
    long = html_to_text("<p>" + "가" * 500 + "</p>", limit=50)
    assert long is not None and len(long) == 50 and long.endswith("…")


# ── scraper: pagination ─────────────────────────────────────────────────────

def test_aiinfrahub_paginates_until_total_reached(monkeypatch):
    monkeypatch.setattr(gpu_portals, "PAGE_SIZE", 1)
    client = _FakeClient({
        0: {"totalCount": 2, "projectDataList": [HUB_OPEN]},
        1: {"totalCount": 2, "projectDataList": [HUB_CLOSED]},
    })
    assert [r.site_id for r in fetch_aiinfrahub(client)] == ["07", "06"]
    assert [c[1]["idx"] for c in client.calls] == [0, 1]


def test_aiinfrahub_incomplete_pagination_is_an_error():
    client = _FakeClient({
        0: {"totalCount": 5, "projectDataList": [HUB_OPEN]},
        1: {"totalCount": 5, "projectDataList": []},   # server ran dry early
    })
    with pytest.raises(ScrapeError, match="incomplete pagination"):
        fetch_aiinfrahub(client)


class _AidcServer:
    """Mirrors the real server: page is 1-based and page=0 aliases page=1."""

    def __init__(self, rows, size):
        self.rows, self.size, self.requested = rows, size, []

    def get(self, url, params=None, **_):
        page = max(1, params["page"])
        self.requested.append(params["page"])
        chunk = self.rows[(page - 1) * self.size: page * self.size]
        total_pages = -(-len(self.rows) // self.size)
        return json.dumps({
            "content": chunk,
            "page": {"totalElements": len(self.rows), "totalPage": total_pages,
                     "number": page - 1, "size": self.size},
        })


def test_aidc_requests_one_based_pages_and_collects_everything(monkeypatch):
    monkeypatch.setattr(gpu_portals, "PAGE_SIZE", 2)
    rows = [_aidc_row(i, f"공고 {i}", "2026-11-01T09:00:00", "2026-12-01T09:00:00")
            for i in (5, 4, 3, 2, 1)]
    server = _AidcServer(rows, size=2)

    assert [r.site_id for r in fetch_aidc(server)] == ["5", "4", "3", "2", "1"]
    assert server.requested == [1, 2, 3]            # never 0


def test_aidc_short_collection_is_an_error_not_a_partial_fresh_list(monkeypatch):
    monkeypatch.setattr(gpu_portals, "PAGE_SIZE", 2)
    rows = [_aidc_row(i, f"공고 {i}", "2026-11-01T09:00:00", "2026-12-01T09:00:00")
            for i in (3, 2, 1)]
    server = _AidcServer(rows, size=2)
    original = server.get

    def flaky(url, params=None, **kw):             # page 2 mysteriously repeats page 1
        return original(url, {**params, "page": 1} if params["page"] == 2 else params, **kw)

    server.get = flaky
    with pytest.raises(ScrapeError, match="incomplete pagination"):
        fetch_aidc(server)


# ── 접수중 필터 ─────────────────────────────────────────────────────────────

def _raw(end):
    return GpuRaw("1", "t", "u", None, end, None, None)


def test_is_open_boundaries():
    assert is_open(_raw(datetime(2026, 11, 23, 3, 0, 1, tzinfo=UTC)), NOW)
    assert not is_open(_raw(datetime(2026, 11, 23, 2, 59, tzinfo=UTC)), NOW)
    assert is_open(_raw(None), NOW)            # 마감일 미상 → 상시 모집으로 간주


# ── adapter helper (fetch_portals) ──────────────────────────────────────────

def _patch_fetchers(monkeypatch, hub, aidc):
    def wrap(f):
        def run(client):
            if isinstance(f, Exception):
                raise f
            return f
        return run
    monkeypatch.setattr(gpu_support, "_PORTALS", (
        ("gpu_aiinfrahub", wrap(hub), "과기정통부·NIPA", None),
        ("gpu_aidc", wrap(aidc), "AI데이터센터(AIDC)", None),
    ))


def _no_cache(source_key, error):
    return [], SourceReport(source_key=source_key, status="failed", item_count=0, error=error)


def _cached_item(source_key, sid):
    return Item(
        stable_id=f"{source_key}:{sid}", source_key=source_key, title="[캐시] 이전 공고",
        detail_url=f"https://example.com/{sid}", badges=(GPU_AI_BADGE,),
    )


def test_portals_keep_only_open_items_with_gpu_badge(monkeypatch):
    hub = parse_aiinfrahub({"projectDataList": [HUB_OPEN, HUB_CLOSED]})
    aidc = parse_aidc({"content": [AIDC_OPEN, AIDC_CLOSED]})
    _patch_fetchers(monkeypatch, hub, aidc)

    items, reports = gpu_support.fetch_portals(_no_cache, now=NOW)

    assert [i.stable_id for i in items] == ["gpu_aiinfrahub:07", "gpu_aidc:532"]
    assert all(GPU_AI_BADGE in i.badges for i in items)
    assert items[0].deadline == date(2026, 11, 24)
    assert items[0].summary.startswith("「첨단 GPU 활용 지원 사업」 — ")
    assert [r.status for r in reports] == ["fresh", "fresh"]


def test_portals_zero_open_items_is_fresh_not_failure(monkeypatch):
    _patch_fetchers(monkeypatch, [], parse_aidc({"content": [AIDC_CLOSED]}))
    items, reports = gpu_support.fetch_portals(_no_cache, now=NOW)
    assert items == []
    assert [r.status for r in reports] == ["fresh", "fresh"]


def test_one_portal_down_falls_back_for_that_portal_only(monkeypatch):
    def fallback(source_key, error):
        assert source_key == "gpu_aiinfrahub" and "TLS" in error
        return [_cached_item(source_key, "03")], SourceReport(
            source_key=source_key, status="fallback", item_count=1, error=error,
        )

    _patch_fetchers(monkeypatch, ScrapeError("simulated TLS failure"),
                    parse_aidc({"content": [AIDC_OPEN]}))
    items, reports = gpu_support.fetch_portals(fallback, now=NOW)

    assert {i.stable_id for i in items} == {"gpu_aiinfrahub:03", "gpu_aidc:532"}
    assert {r.source_key: r.status for r in reports} == {
        "gpu_aiinfrahub": "fallback", "gpu_aidc": "fresh",
    }


def test_portal_failure_without_cache_is_reported_failed(monkeypatch):
    _patch_fetchers(monkeypatch, ScrapeError("outage"), parse_aidc({"content": [AIDC_OPEN]}))
    items, reports = gpu_support.fetch_portals(_no_cache, now=NOW)
    assert {r.source_key: r.status for r in reports}["gpu_aiinfrahub"] == "failed"
    assert [i.stable_id for i in items] == ["gpu_aidc:532"]


# ── gov-support integration ─────────────────────────────────────────────────

@pytest.fixture
def _offline_gov(monkeypatch):
    from src.scrapers.bizinfo import BizinfoListResult
    from src.scrapers.iris import IrisListResult
    from src.scrapers.ntis import NtisListResult
    monkeypatch.setattr(gov_support, "fetch_bizinfo",
                        lambda client: BizinfoListResult(items=[], complete=True))
    monkeypatch.setattr(gov_support, "fetch_nipa", lambda client: [])
    monkeypatch.setattr(gov_support, "fetch_iris",
                        lambda client: IrisListResult(items=[], complete=True))
    monkeypatch.setattr(gov_support, "fetch_ntis",
                        lambda client: NtisListResult(items=[], complete=True))


def test_gov_support_adapter_includes_gpu_portal_items_and_reports(monkeypatch, _offline_gov):
    item = _cached_item("gpu_aidc", "532")
    report = SourceReport(source_key="gpu_aidc", status="fresh", item_count=1)
    monkeypatch.setattr(gov_support, "fetch_gpu_portals", lambda fallback: ([item], [report]))

    result = gov_support.fetch_result()

    assert item in result.items
    assert report in result.source_reports


def test_gov_support_with_failed_gpu_portal_is_unusable(monkeypatch, _offline_gov):
    failed = SourceReport(source_key="gpu_aiinfrahub", status="failed", item_count=0, error="x")
    monkeypatch.setattr(gov_support, "fetch_gpu_portals", lambda fallback: ([], [failed]))
    assert not gov_support.fetch_result().usable


def test_gpu_portals_belong_to_gov_support_only():
    assert set(GPU_PORTAL_SOURCES) <= set(ARCHIVES["gov-support"].sources)
    assert all(k in SOURCES for k in GPU_PORTAL_SOURCES)
    assert "gpu-support" not in ARCHIVES
    for key, cfg in ARCHIVES.items():
        if key != "gov-support":
            assert not set(GPU_PORTAL_SOURCES) & set(cfg.sources)


def test_source_id_rules_extract_ids():
    assert SOURCES["gpu_aidc"].id_rule("https://aidc.atops.or.kr/announcements/532") == "532"
    assert SOURCES["gpu_aiinfrahub"].id_rule("https://aiinfrahub.kr/project/07") == "07"


# ── dedicated section in the document ───────────────────────────────────────

TODAY = date(2026, 11, 23)


def _portal_item(sid, deadline, source="gpu_aidc"):
    return Item(
        stable_id=f"{source}:{sid}", source_key=source, title=f"포털 공고 {sid}",
        detail_url=f"https://example.com/{sid}", deadline=deadline,
        badges=(GPU_AI_BADGE,),
    )


def test_portal_items_get_their_own_section_first_and_sorted():
    a = _portal_item("1", date(2026, 12, 4))
    b = _portal_item("2", date(2026, 11, 24), source="gpu_aiinfrahub")
    sections = classify_for_curation(items_new=[a, b], items_ongoing=[], items_expired=[], today=TODAY)

    assert sections[0].key == SEC_GPU_PORTAL
    assert [i.stable_id for i in sections[0].items] == ["gpu_aiinfrahub:2", "gpu_aidc:1"]
    # no other section repeats them
    others = [i.stable_id for s in sections[1:] for i in s.items]
    assert not {"gpu_aidc:1", "gpu_aiinfrahub:2"} & set(others)


def test_other_sources_gpu_items_are_not_pulled_into_portal_section():
    kw = Item(stable_id="bizinfo:1", source_key="bizinfo", title="GPU 클라우드 지원",
              detail_url="https://example.com/b", badges=(GPU_AI_BADGE,))
    sections = {s.key: s for s in classify_for_curation(
        items_new=[kw], items_ongoing=[], items_expired=[], today=TODAY)}
    assert sections[SEC_GPU_PORTAL].items == []
    assert [i.stable_id for i in sections[curation.SEC_NEW_GPU].items] == ["bizinfo:1"]


def test_portal_section_is_uncapped_and_rendered_in_archive_and_mail():
    from src.render.daily_html import render_daily_html

    items = [_portal_item(str(n), date(2026, 12, 1)) for n in range(60)]
    for for_email in (True, False):
        html = render_daily_html(
            ARCHIVES["gov-support"], items, [], TODAY, for_email=for_email,
        )
        assert "GPU 지원사업 공고 — AI컴퓨팅자원 포털·AIDC (60건)" in html
        assert html.count("포털 공고 ") >= 60


# ── extra CA support in the shared HTTP client ──────────────────────────────

def test_http_client_extra_ca_keeps_verification_on():
    import certifi
    with HttpClient(extra_ca_file=Path(certifi.where())) as client:
        assert client._client is not None


def test_http_client_default_unchanged():
    with HttpClient() as client:
        assert client.extra_ca_file is None


def test_bundled_intermediate_ca_is_valid_pem():
    import ssl
    pem = gpu_support._AIINFRAHUB_CA
    assert pem.is_file()
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cafile=str(pem))      # raises on malformed PEM
