from src.scrapers import bizinfo
from src.scrapers.base import HttpClient, ScrapeError


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

    def __init__(self, pages: dict[int, str], fail_pages: set[int]):
        self._pages = pages
        self._fail_pages = fail_pages
        self._client = None

    def get(self, url, params=None, timeout=None):
        page = params["cpage"]
        if page in self._fail_pages:
            raise ScrapeError(f"failed after 3 attempts: {url} (last error: ReadTimeout: simulated)")
        return self._pages.get(page, _empty_page_html())


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
