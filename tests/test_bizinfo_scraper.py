import io
import json
import zipfile
from xml.sax.saxutils import escape

import pytest

from src.scrapers import bizinfo
from src.scrapers.base import HttpClient, ScrapeError


@pytest.fixture(autouse=True)
def _disable_real_api_key(monkeypatch):
    monkeypatch.delenv("BIZINFO_API_KEY", raising=False)


def _page_html(pblanc_ids: list[str]) -> str:
    """Build a minimal valid bizinfo list-page fragment with one row per id."""
    rows = "".join(
        f'<tr><td>{i}</td><td>지원분야</td>'
        f'<td><a href="/sii/siia/selectSIIA200Detail.do?pblancId={pid}">title-{pid}</a></td>'
        f'<td>2026-01-01 ~ 2026-12-31</td><td>주관기관</td><td>수행기관</td></tr>'
        for i, pid in enumerate(pblanc_ids)
    )
    return f'<div class="table_Type_1"><table><tbody>{rows}</tbody></table></div>'


def _empty_page_html() -> str:
    return '<div class="table_Type_1"><table><tbody></tbody></table></div>'


class _FakeHttpClient(HttpClient):
    """Serves canned page responses by `cpage`, raising ScrapeError for pages
    listed in `fail_pages` (simulating an exhausted-retries page failure)."""

    def __init__(
        self, pages: dict[int, str], fail_pages: set[int], excel: bytes | None = None,
    ):
        self._pages = pages
        self._fail_pages = fail_pages
        self._excel = excel
        self._client = None

    def get(self, url, params=None, timeout=None, attempts=None):
        page = params["cpage"]
        if page in self._fail_pages:
            raise ScrapeError(f"failed after 3 attempts: {url} (last error: ReadTimeout: simulated)")
        return self._pages.get(page, _empty_page_html())

    def get_bytes(self, url, params=None, timeout=None, attempts=None):
        if self._excel is None:
            raise ScrapeError("simulated Excel outage")
        return self._excel


def _xlsx_bytes(data_rows: list[list[str]]) -> bytes:
    headers = [
        "번호", "소관부처", "사업수행기관", "지원분야", "공고명",
        "신청시작일자", "신청종료일자", "등록일자", "공고상세URL",
    ]

    def xml_row(number: int, values: list[str]) -> str:
        cells = "".join(
            f'<c r="{chr(65 + index)}{number}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
            for index, value in enumerate(values)
        )
        return f'<row r="{number}">{cells}</row>'

    rows = xml_row(1, headers) + "".join(
        xml_row(index, values) for index, values in enumerate(data_rows, start=2)
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{rows}</sheetData></worksheet>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as workbook:
        workbook.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def test_fetch_listings_prefers_single_request_excel_download():
    pblanc_id = "PBLN_000000000000777"
    excel = _xlsx_bytes([[
        "1", "중소벤처기업부", "전담기관", "기술", "엑셀 공고",
        "2026-08-01", "2026-08-31", "2026-08-02",
        f"https://www.bizinfo.go.kr/x?pblancId={pblanc_id}",
    ]])
    client = _FakeHttpClient({}, fail_pages=set(), excel=excel)

    result = bizinfo.fetch_listings(client)

    assert result.complete is True
    assert result.channel == "excel"
    assert len(result.items) == 1
    assert result.items[0].pblanc_id == pblanc_id
    assert result.items[0].apply_period == "2026-08-01 ~ 2026-08-31"


@pytest.mark.parametrize("response_shape", ["current-list", "legacy-item-wrapper"])
def test_official_api_parser_validates_total_and_keeps_enrichment(response_shape):
    record = {
        "pblancId": "PBLN_000000000000888",
        "pblancNm": "AI 제조 지원",
        "jrsdInsttNm": "중소벤처기업부",
        "excInsttNm": "전담기관",
        "pldirSportRealmLclasCodeNm": "기술",
        "reqstBeginEndDe": "20260801 ~ 20260831",
        "bsnsSumryCn": "<div>인공지능 사업화 지원</div>",
        "trgetNm": "중소기업",
        "totCnt": "1",
    }
    record["hashtags" if response_shape == "current-list" else "hashTags"] = "AI,부산"
    json_array = [record] if response_shape == "current-list" else {"item": [record]}

    class ApiClient:
        def get(self, url, params=None, timeout=None):
            return json.dumps({
                "jsonArray": json_array,
            })

    items = bizinfo._fetch_official_api(ApiClient(), "key")

    assert len(items) == 1
    assert items[0].deadline.isoformat() == "2026-08-31"
    assert items[0].summary == "인공지능 사업화 지원"
    assert items[0].target == "중소기업"
    assert items[0].hashtags == ("AI", "부산")


def test_fetch_listings_merges_api_enrichment_with_excel_dates(monkeypatch):
    monkeypatch.setenv("BIZINFO_API_KEY", "configured-test-key")
    pblanc_id = "PBLN_000000000000889"
    excel = _xlsx_bytes([[
        "1", "중소벤처기업부", "전담기관", "기술", "API 병합 공고",
        "2026-08-01", "2026-08-31", "2026-08-02",
        f"https://www.bizinfo.go.kr/x?pblancId={pblanc_id}",
    ]])

    class ApiAndExcelClient:
        def __init__(self):
            self.excel_attempts: list[int | None] = []

        def get(self, url, params=None, timeout=None):
            return json.dumps({"jsonArray": [{
                "pblancId": pblanc_id,
                "pblancNm": "API 병합 공고",
                "jrsdInsttNm": "중소벤처기업부",
                "excInsttNm": "전담기관",
                "pldirSportRealmLclasCodeNm": "기술",
                "reqstBeginEndDe": "상시 접수",
                "bsnsSumryCn": "API 사업 요약",
                "trgetNm": "중소기업",
                "hashtags": "AI,부산",
                "totCnt": "1",
            }]})

        def get_bytes(self, url, params=None, timeout=None, attempts=None):
            self.excel_attempts.append(attempts)
            return excel

    client = ApiAndExcelClient()

    result = bizinfo.fetch_listings(client)

    assert result.channel == "api+excel"
    assert result.complete is True
    assert len(result.items) == 1
    item = result.items[0]
    assert item.apply_period == "2026-08-01 ~ 2026-08-31"
    assert item.deadline.isoformat() == "2026-08-31"
    assert item.summary == "API 사업 요약"
    assert item.target == "중소기업"
    assert item.hashtags == ("AI", "부산")
    assert client.excel_attempts == [1]


def test_fetch_listings_uses_complete_api_when_excel_enrichment_fails(monkeypatch):
    monkeypatch.setenv("BIZINFO_API_KEY", "configured-test-key")
    pblanc_id = "PBLN_000000000000890"

    class ApiOnlyClient:
        def __init__(self):
            self.get_calls = 0

        def get(self, url, params=None, timeout=None):
            self.get_calls += 1
            return json.dumps({"jsonArray": [{
                "pblancId": pblanc_id,
                "pblancNm": "API 단독 공고",
                "reqstBeginEndDe": "상시 접수",
                "totCnt": "1",
            }]})

        def get_bytes(self, url, params=None, timeout=None, attempts=None):
            assert attempts == 1
            raise ScrapeError("simulated Excel enrichment outage")

    client = ApiOnlyClient()

    result = bizinfo.fetch_listings(client)

    assert result.channel == "api"
    assert result.complete is True
    assert [item.pblanc_id for item in result.items] == [pblanc_id]
    assert client.get_calls == 1


def test_fetch_listings_complete_when_all_pages_succeed():
    pages = {1: _page_html(["PBLN_000000000000001", "PBLN_000000000000002"])}
    client = _FakeHttpClient(pages, fail_pages=set())
    result = bizinfo.fetch_listings(client)
    assert result.complete is True
    assert [i.pblanc_id for i in result.items] == [
        "PBLN_000000000000001", "PBLN_000000000000002",
    ]


def test_fetch_listings_first_page_failure_raises():
    """No data at all was ever collected — must still raise so the caller's
    fallback-to-cache path fires, matching the pre-existing total-failure
    behavior."""
    client = _FakeHttpClient({}, fail_pages={1})
    try:
        bizinfo.fetch_listings(client)
        assert False, "expected ScrapeError"
    except ScrapeError:
        pass


def test_fetch_listings_midway_failure_returns_partial_incomplete():
    """Pages 1-2 succeed, page 3 fails after retries — pagination stops early
    and whatever was gathered is returned, but flagged incomplete so the
    caller does NOT treat it as today's full list."""
    pages = {
        1: _page_html(["PBLN_000000000000001"]),
        2: _page_html(["PBLN_000000000000002"]),
    }
    client = _FakeHttpClient(pages, fail_pages={3})
    result = bizinfo.fetch_listings(client)
    assert result.complete is False
    assert [i.pblanc_id for i in result.items] == [
        "PBLN_000000000000001", "PBLN_000000000000002",
    ]


def test_fetch_listings_stops_normally_on_empty_page():
    pages = {
        1: _page_html(["PBLN_000000000000001"]),
        2: _empty_page_html(),
    }
    client = _FakeHttpClient(pages, fail_pages=set())
    result = bizinfo.fetch_listings(client)
    assert result.complete is True
    assert [i.pblanc_id for i in result.items] == ["PBLN_000000000000001"]


@pytest.mark.parametrize(
    ("error_kind", "error_text"),
    [
        ("ConnectTimeout", "timed out"),
        ("ConnectError", "[Errno -3] Temporary failure in name resolution"),
    ],
)
def test_fetch_listings_retries_excel_after_origin_outage(
    monkeypatch, error_kind, error_text,
):
    pblanc_id = "PBLN_000000000000999"
    excel = _xlsx_bytes([[
        "1", "중소벤처기업부", "전담기관", "기술", "지연 재시도 공고",
        "2026-08-01", "2026-08-31", "2026-08-02",
        f"https://www.bizinfo.go.kr/x?pblancId={pblanc_id}",
    ]])
    sleeps: list[float] = []

    class RecoveringClient(_FakeHttpClient):
        def __init__(self):
            super().__init__({}, fail_pages={1})
            self.excel_calls = 0
            self.excel_attempts: list[int | None] = []
            self.html_calls = 0

        def get(self, url, params=None, timeout=None, attempts=None):
            self.html_calls += 1
            raise ScrapeError(
                f"failed (last error: {error_kind}: {error_text})",
                kind=error_kind,
                transient=True,
            )

        def get_bytes(self, url, params=None, timeout=None, attempts=None):
            self.excel_calls += 1
            self.excel_attempts.append(attempts)
            if self.excel_calls <= len(bizinfo.BASE_MIRRORS):
                raise ScrapeError(
                    f"failed (last error: {error_kind}: {error_text})",
                    kind=error_kind,
                    transient=True,
                )
            return excel

    monkeypatch.setattr(bizinfo.time, "sleep", sleeps.append)
    client = RecoveringClient()

    result = bizinfo.fetch_listings(client)

    assert result.complete is True
    assert result.channel == "excel"
    assert [item.pblanc_id for item in result.items] == [pblanc_id]
    assert sleeps == [bizinfo.OUTAGE_RETRY_DELAY_SECONDS]
    assert client.excel_calls == len(bizinfo.BASE_MIRRORS) + 1
    assert client.excel_attempts == [None, bizinfo.BACKUP_HOST_ATTEMPTS, None]
    assert client.html_calls == 0


def test_list_page_timeout_can_be_overridden_for_diagnostics(monkeypatch):
    monkeypatch.setenv("BIZINFO_LIST_PAGE_TIMEOUT", "45")

    assert bizinfo._list_page_timeout() == 45.0


@pytest.mark.parametrize("value", ["zero", "0", "121"])
def test_list_page_timeout_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("BIZINFO_LIST_PAGE_TIMEOUT", value)

    with pytest.raises(ScrapeError, match="BIZINFO_LIST_PAGE_TIMEOUT"):
        bizinfo._list_page_timeout()
