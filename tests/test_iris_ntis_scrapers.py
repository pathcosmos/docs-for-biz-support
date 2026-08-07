from src.scrapers import iris, ntis
from src.scrapers.base import ScrapeError


class _FakeClient:
    def __init__(self, pages: dict[int, str], fail_pages: set[int] | None = None):
        self.pages = pages
        self.fail_pages = fail_pages or set()
        self.calls: list[dict] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params or {})
        page = (params or {}).get("pageIndex", 1)
        if page in self.fail_pages:
            raise ScrapeError(f"page {page} failed")
        return self.pages[page]


def _iris_page(current: int, total: int, rows: list[tuple[str, str, str]]) -> str:
    items = "".join(
        '<li>'
        f'<span class="inst_title">과기정통부 &gt; {organizer}</span>'
        '<strong class="title">'
        f'<a onclick="f_bsnsAncmListForm_view(\'{ancm_id}\',\'2026\','
        f"'S001','1','3','2026/08/01','{end_date}'); return false;\">"
        f"IRIS {ancm_id}</a></strong>"
        f'<span class="period">2026/08/01~{end_date}</span>'
        "</li>"
        for ancm_id, organizer, end_date in rows
    )
    return (
        f'<span class="current_page">현재 페이지 <strong>{current}</strong>/{total}</span>'
        f'<div class="tstyle list biz_announce"><ul class="dbody">{items}</ul></div>'
    )


def _ntis_page(total_count: int, rows: list[tuple[str, str, str, str]]) -> str:
    body = "".join(
        "<tr>"
        '<td data-title="현황"><span>접수중</span></td>'
        '<td data-title="공고명" class="tl">'
        f'<a href="/rndgate/eg/un/ra/view.do?roRndUid={uid}&amp;flag=rndList" '
        f'title="{title}">{title}</a></td>'
        f'<td data-title="부처명">{organizer}</td>'
        '<td data-title="접수일">2026.08.01</td>'
        f'<td data-title="마감일">{end_date}</td>'
        "</tr>"
        for uid, title, organizer, end_date in rows
    )
    return (
        f'<input id="totalCount" value="{total_count}">'
        f"<table><tbody>{body}</tbody></table>"
    )


def test_iris_walks_pages_and_keeps_later_duplicate_deadline():
    client = _FakeClient({
        1: _iris_page(1, 2, [("019614", "전문기관", "2026/08/13")]),
        2: _iris_page(2, 2, [
            ("019614", "전문기관", "2026/08/14"),
            ("023377", "정보통신산업진흥원", "2026/08/20"),
        ]),
    })

    result = iris.fetch_listings(client)

    assert result.complete is True
    assert [item.ancm_id for item in result.items] == ["019614", "023377"]
    assert result.items[0].deadline.isoformat() == "2026-08-14"
    assert client.calls == [{"pageIndex": 1}, {"pageIndex": 2}]


def test_iris_midway_failure_is_incomplete():
    client = _FakeClient(
        {1: _iris_page(1, 2, [("019614", "전문기관", "2026/08/13")])},
        fail_pages={2},
    )
    result = iris.fetch_listings(client)
    assert result.complete is False
    assert [item.ancm_id for item in result.items] == ["019614"]


def test_ntis_only_requests_upcoming_and_open_statuses():
    page1_rows = [
        (str(1000 + i), f"공고 {i}", "산업통상부", "2026.08.18")
        for i in range(10)
    ]
    client = _FakeClient({
        1: _ntis_page(11, page1_rows),
        2: _ntis_page(11, [("2000", "마지막 공고", "중소벤처기업부", "2026.08.20")]),
    })

    result = ntis.fetch_listings(client)

    assert result.complete is True
    assert len(result.items) == 11
    assert result.items[-1].rnd_uid == "2000"
    assert result.items[-1].deadline.isoformat() == "2026-08-20"
    assert client.calls == [
        {"searchStatusList": "P,B", "pageIndex": 1},
        {"searchStatusList": "P,B", "pageIndex": 2},
    ]


def test_ntis_midway_failure_is_incomplete():
    client = _FakeClient(
        {1: _ntis_page(11, [("1000", "공고", "과기정통부", "2026.08.18")])},
        fail_pages={2},
    )
    result = ntis.fetch_listings(client)
    assert result.complete is False
    assert [item.rnd_uid for item in result.items] == ["1000"]
