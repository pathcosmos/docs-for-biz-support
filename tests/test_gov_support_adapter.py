from src import db
from src.adapters import gov_support
from src.scrapers.base import ScrapeError
from src.scrapers.bizinfo import BizinfoListResult, BizinfoRaw


def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "LOCAL_DB_PATH", tmp_path / "test.db")
    client = db.connect()
    db.migrate(client)
    return client


def _seed_cached_bizinfo_item(client):
    client.execute(
        db._UPSERT_ITEM_SQL,
        db._item_row(
            db._item_from_db_row((
                "bizinfo:PBLN_000000000999999", "bizinfo", "[캐시] 이전에 성공한 공고",
                "https://www.bizinfo.go.kr/x", None, "주관기관", "1천만원",
                "2026-07-01 ~ 2026-08-01", None, "전국", "중소기업", "요약", None,
            )),
            "gov-support", first_seen="2026-07-01", last_seen="2026-07-01",
        ),
    )


def test_total_failure_falls_back_to_cached_snapshot(tmp_path, monkeypatch):
    client = _isolated_db(tmp_path, monkeypatch)
    _seed_cached_bizinfo_item(client)
    client.close()

    monkeypatch.setattr(
        gov_support, "fetch_bizinfo",
        lambda client: (_ for _ in ()).throw(ScrapeError("simulated total outage")),
    )
    monkeypatch.setattr(gov_support, "fetch_nipa", lambda client: [])

    items = gov_support.fetch()
    assert [i.stable_id for i in items] == ["bizinfo:PBLN_000000000999999"]


def test_incomplete_fetch_falls_back_instead_of_shipping_partial_data(tmp_path, monkeypatch):
    """A page failure partway through pagination must NOT be trusted as
    today's real (small) list — it should fall back to the cached snapshot
    exactly like a total failure, per the PR-PARTIAL-FETCH fix."""
    client = _isolated_db(tmp_path, monkeypatch)
    _seed_cached_bizinfo_item(client)
    client.close()

    partial_result = BizinfoListResult(
        items=[
            BizinfoRaw(
                pblanc_id="PBLN_000000000111111", title="부분 수집된 공고",
                apply_period=None, deadline=None, organizer=None,
                executor=None, support_field=None,
            ),
        ],
        complete=False,
    )
    monkeypatch.setattr(gov_support, "fetch_bizinfo", lambda client: partial_result)
    monkeypatch.setattr(gov_support, "fetch_nipa", lambda client: [])

    items = gov_support.fetch()
    ids = [i.stable_id for i in items]
    # The cached (complete, previously-successful) snapshot wins — the
    # partially-scraped item from today's broken run must NOT appear.
    assert ids == ["bizinfo:PBLN_000000000999999"]
    assert "bizinfo:PBLN_000000000111111" not in ids


def test_complete_fetch_is_used_as_is_no_fallback(tmp_path, monkeypatch):
    client = _isolated_db(tmp_path, monkeypatch)
    _seed_cached_bizinfo_item(client)
    client.close()

    complete_result = BizinfoListResult(
        items=[
            BizinfoRaw(
                pblanc_id="PBLN_000000000222222", title="정상 수집된 오늘 공고",
                apply_period=None, deadline=None, organizer=None,
                executor=None, support_field=None,
            ),
        ],
        complete=True,
    )
    monkeypatch.setattr(gov_support, "fetch_bizinfo", lambda client: complete_result)
    monkeypatch.setattr(gov_support, "fetch_nipa", lambda client: [])

    items = gov_support.fetch()
    ids = [i.stable_id for i in items]
    assert ids == ["bizinfo:PBLN_000000000222222"]
    # Yesterday's cached-only item must NOT be resurrected when today's
    # fetch is genuinely complete (even though it's a different/smaller set —
    # that's a legitimate diff outcome, not a scrape failure).
    assert "bizinfo:PBLN_000000000999999" not in ids
