from src.scrapers import kstartup
from src.scrapers.base import HttpClient, ScrapeError


def _page_html(site_ids: list[str]) -> str:
    items = "".join(
        f'<li><a href="#none" onclick="fn_goView(\'{sid}\')">'
        f'<div class="gallery_info"><div class="txt_sec">'
        f'<div class="gallery_tit">title-{sid}</div></div>'
        f'<div class="date_sec"><div class="sub_tit">summary-{sid}</div></div>'
        f'</div></a></li>'
        for sid in site_ids
    )
    return f'<ul class="gallery_list">{items}</ul>'


def _empty_page_html() -> str:
    return '<ul class="gallery_list"></ul>'


class _FakeHttpClient(HttpClient):
    def __init__(self, pages: dict[int, str], fail_pages: set[int]):
        self._pages = pages
        self._fail_pages = fail_pages
        self._client = None

    def get(self, url, params=None, timeout=None):
        page = params["page"]
        if page in self._fail_pages:
            raise ScrapeError(f"failed after 3 attempts: {url} (last error: ReadTimeout: simulated)")
        return self._pages.get(page, _empty_page_html())


def test_fetch_listings_complete_when_all_pages_succeed():
    pages = {1: _page_html(["171020", "171021"])}
    client = _FakeHttpClient(pages, fail_pages=set())
    result = kstartup.fetch_listings(client, "webCMRCZN.do")
    assert result.complete is True
    assert [i.site_id for i in result.items] == ["171020", "171021"]


def test_fetch_listings_first_page_failure_raises():
    client = _FakeHttpClient({}, fail_pages={1})
    try:
        kstartup.fetch_listings(client, "webCMRCZN.do")
        assert False, "expected ScrapeError"
    except ScrapeError:
        pass


def test_fetch_listings_midway_failure_returns_partial_incomplete():
    pages = {1: _page_html(["171020"]), 2: _page_html(["171021"])}
    client = _FakeHttpClient(pages, fail_pages={3})
    result = kstartup.fetch_listings(client, "webCMRCZN.do")
    assert result.complete is False
    assert [i.site_id for i in result.items] == ["171020", "171021"]


def test_fetch_listings_rejects_unknown_endpoint():
    client = _FakeHttpClient({}, fail_pages=set())
    try:
        kstartup.fetch_listings(client, "not-a-real-endpoint")
        assert False, "expected ValueError"
    except ValueError:
        pass
