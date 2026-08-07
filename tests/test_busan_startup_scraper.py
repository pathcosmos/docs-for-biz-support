import json

from src.scrapers import busan_startup
from src.scrapers.base import ScrapeError


class _FakeClient:
    def __init__(self, pages: dict[int, str], fail_pages: set[int] | None = None):
        self.pages = pages
        self.fail_pages = fail_pages or set()
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params or {})
        page = params["pageNo"]
        if page in self.fail_pages:
            raise ScrapeError("simulated failure")
        return self.pages[page]


def _page(total: int, final: int, rows: list[dict]) -> str:
    return json.dumps({
        "result": {"resultCode": 200, "resultMsg": "OK"},
        "list": rows,
        "pageInfo": {"totalCount": total, "finalPageNo": final},
    })


def _row(code: str, *, always_open: bool = False) -> dict:
    return {
        "busi_code": code,
        "busi_title": f"부산 지원사업 {code}",
        "busi_gubun": "시설·공간",
        "busi_gubun_code": "2",
        "busi_comp": "부산기술창업투자원",
        "appl_type": "전체창업자",
        "appl_dtype": "Y" if always_open else "N",
        "appl_sdate": None if always_open else "2026-08-01",
        "appl_edate": None if always_open else "2026-08-21",
    }


def test_fetches_every_active_page_with_fixed_filter():
    first = [_row(str(index)) for index in range(1, 11)]
    client = _FakeClient({
        1: _page(11, 2, first),
        2: _page(11, 2, [_row("11", always_open=True)]),
    })

    result = busan_startup.fetch_listings(client)

    assert result.complete is True
    assert len(result.items) == 11
    assert result.items[0].deadline.isoformat() == "2026-08-21"
    assert result.items[-1].apply_period == "상시"
    assert result.items[-1].deadline is None
    assert [call["pageNo"] for call in client.calls] == [1, 2]
    assert all(call["deadline"] == "N" for call in client.calls)


def test_midway_failure_returns_incomplete_partial_result():
    client = _FakeClient(
        {1: _page(11, 2, [_row("1")])},
        fail_pages={2},
    )
    result = busan_startup.fetch_listings(client)
    assert result.complete is False
    assert [item.business_code for item in result.items] == ["1"]


def test_count_mismatch_is_not_accepted_as_complete():
    client = _FakeClient({1: _page(2, 1, [_row("1")])})
    result = busan_startup.fetch_listings(client)
    assert result.complete is False
