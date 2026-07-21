from src import db
from src.adapters import kstartup_biz, kstartup_global, kstartup_mentoring
from src.scrapers.base import ScrapeError
from src.scrapers.kstartup import KStartupListResult, KStartupRaw


def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "LOCAL_DB_PATH", tmp_path / "test.db")
    client = db.connect()
    db.migrate(client)
    return client


def _seed_item(client, archive_key, source_key, stable_id, category, title):
    client.execute(
        db._UPSERT_ITEM_SQL,
        db._item_row(
            db._item_from_db_row((
                stable_id, source_key, title,
                f"https://www.k-startup.go.kr/x?id={stable_id}", category,
                "창업진흥원", None, None, None, None, None, None, None,
            )),
            archive_key, first_seen="2026-07-01", last_seen="2026-07-01",
        ),
    )


# ── kstartup-biz (single endpoint) ──────────────────────────────────────────

def test_biz_total_failure_falls_back_to_cache(tmp_path, monkeypatch):
    client = _isolated_db(tmp_path, monkeypatch)
    _seed_item(client, "kstartup-biz", "kstartup_biz", "kstartup_biz:999", None, "[캐시] 이전 공고")
    client.close()

    monkeypatch.setattr(
        kstartup_biz, "fetch_listings",
        lambda client, endpoint: (_ for _ in ()).throw(ScrapeError("simulated outage")),
    )
    items = kstartup_biz.fetch()
    assert [i.stable_id for i in items] == ["kstartup_biz:999"]


def test_biz_incomplete_falls_back_instead_of_partial(tmp_path, monkeypatch):
    client = _isolated_db(tmp_path, monkeypatch)
    _seed_item(client, "kstartup-biz", "kstartup_biz", "kstartup_biz:999", None, "[캐시] 이전 공고")
    client.close()

    partial = KStartupListResult(
        items=[KStartupRaw(site_id="111", title="부분 수집", summary=None)],
        complete=False,
    )
    monkeypatch.setattr(kstartup_biz, "fetch_listings", lambda client, endpoint: partial)
    items = kstartup_biz.fetch()
    ids = [i.stable_id for i in items]
    assert ids == ["kstartup_biz:999"]
    assert "kstartup_biz:111" not in ids


def test_biz_complete_fetch_used_as_is(tmp_path, monkeypatch):
    client = _isolated_db(tmp_path, monkeypatch)
    _seed_item(client, "kstartup-biz", "kstartup_biz", "kstartup_biz:999", None, "[캐시] 이전 공고")
    client.close()

    complete = KStartupListResult(
        items=[KStartupRaw(site_id="222", title="정상 수집", summary=None)],
        complete=True,
    )
    monkeypatch.setattr(kstartup_biz, "fetch_listings", lambda client, endpoint: complete)
    items = kstartup_biz.fetch()
    ids = [i.stable_id for i in items]
    assert ids == ["kstartup_biz:222"]
    assert "kstartup_biz:999" not in ids


# ── kstartup-mentoring (two endpoints: rnd / mentoring) ─────────────────────

def test_mentoring_one_endpoint_failure_does_not_discard_the_other(tmp_path, monkeypatch):
    """webRND.do fails -> falls back to cached 'rnd' items only. webMNT_CNS.do
    succeeds -> its fresh data must survive, not be discarded or duplicated."""
    client = _isolated_db(tmp_path, monkeypatch)
    _seed_item(client, "kstartup-mentoring", "kstartup_mentoring",
               "kstartup_mentoring:rnd-cached", "rnd", "[캐시] R&D 공고")
    _seed_item(client, "kstartup-mentoring", "kstartup_mentoring",
               "kstartup_mentoring:mnt-cached", "mentoring", "[캐시] 멘토링 공고 (구버전)")
    client.close()

    def fake_fetch(client, endpoint):
        if endpoint == "webRND.do":
            raise ScrapeError("simulated rnd outage")
        return KStartupListResult(
            items=[KStartupRaw(site_id="mnt-fresh", title="신규 멘토링 공고", summary=None)],
            complete=True,
        )

    monkeypatch.setattr(kstartup_mentoring, "fetch_listings", fake_fetch)
    items = kstartup_mentoring.fetch()
    ids = {i.stable_id for i in items}

    # rnd side: fell back to cache (only the rnd-category cached item, not mentoring's).
    assert "kstartup_mentoring:rnd-cached" in ids
    # mentoring side: used the fresh scrape, not the stale cached mentoring item.
    assert "kstartup_mentoring:mnt-fresh" in ids
    assert "kstartup_mentoring:mnt-cached" not in ids
    assert len(items) == 2


def test_mentoring_both_endpoints_complete_no_fallback_used(tmp_path, monkeypatch):
    client = _isolated_db(tmp_path, monkeypatch)
    _seed_item(client, "kstartup-mentoring", "kstartup_mentoring",
               "kstartup_mentoring:stale", "rnd", "[캐시] 안 쓰여야 함")
    client.close()

    def fake_fetch(client, endpoint):
        site_id = "rnd-1" if endpoint == "webRND.do" else "mnt-1"
        return KStartupListResult(
            items=[KStartupRaw(site_id=site_id, title=f"fresh-{site_id}", summary=None)],
            complete=True,
        )

    monkeypatch.setattr(kstartup_mentoring, "fetch_listings", fake_fetch)
    items = kstartup_mentoring.fetch()
    ids = {i.stable_id for i in items}
    assert ids == {"kstartup_mentoring:rnd-1", "kstartup_mentoring:mnt-1"}


# ── kstartup-global (two endpoints: facility / global) ──────────────────────

def test_global_one_endpoint_incomplete_falls_back_for_that_category_only(tmp_path, monkeypatch):
    client = _isolated_db(tmp_path, monkeypatch)
    _seed_item(client, "kstartup-global", "kstartup_global",
               "kstartup_global:fac-cached", "facility", "[캐시] 시설 공고")
    _seed_item(client, "kstartup-global", "kstartup_global",
               "kstartup_global:glo-cached", "global", "[캐시] 글로벌 공고 (구버전)")
    client.close()

    def fake_fetch(client, endpoint):
        if endpoint == "webFC_SP_NR.do":
            return KStartupListResult(
                items=[KStartupRaw(site_id="fac-partial", title="부분 수집", summary=None)],
                complete=False,
            )
        return KStartupListResult(
            items=[KStartupRaw(site_id="glo-fresh", title="신규 글로벌 공고", summary=None)],
            complete=True,
        )

    monkeypatch.setattr(kstartup_global, "fetch_listings", fake_fetch)
    items = kstartup_global.fetch()
    ids = {i.stable_id for i in items}

    assert ids == {"kstartup_global:fac-cached", "kstartup_global:glo-fresh"}
