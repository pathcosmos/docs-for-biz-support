from datetime import date

from src import db, diff
from src.config.archives import ARCHIVES
from src.models import Item


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "LOCAL_DB_PATH", tmp_path / "pipeline.db")
    client = db.connect()
    db.migrate(client)
    return client


def _item(stable_id: str, **kwargs) -> Item:
    data = {
        "stable_id": stable_id,
        "source_key": "bizinfo",
        "title": stable_id,
        "detail_url": f"https://example.com/{stable_id}",
    }
    data.update(kwargs)
    return Item(**data)


def _record(client, snapshot_date, baseline_date, items):
    cfg = ARCHIVES["gov-support"]
    db.start_scrape_run(client, cfg.key, snapshot_date, baseline_date)
    result = diff.classify(client, cfg, snapshot_date, baseline_date, items)
    db.record_daily(
        client,
        cfg.key,
        snapshot_date,
        items,
        {i.stable_id for i in result.new},
        {i.stable_id for i in result.ongoing},
        {i.stable_id for i in result.expired},
    )
    return result


def test_latest_completed_snapshot_bridges_a_missing_day(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    try:
        first = date(2026, 8, 1)
        db.insert_seed_snapshot(client, "gov-support", first, [_item("a")])
        assert db.get_latest_completed_snapshot_date(
            client, "gov-support", date(2026, 8, 3)
        ) == first

        result = _record(client, date(2026, 8, 3), first, [_item("a"), _item("b")])
        assert {i.stable_id for i in result.ongoing} == {"a"}
        assert {i.stable_id for i in result.new} == {"b"}
    finally:
        client.close()


def test_same_day_rerun_replaces_status_rows(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    try:
        baseline = date(2026, 8, 1)
        today = date(2026, 8, 2)
        db.insert_seed_snapshot(client, "gov-support", baseline, [_item("a")])
        _record(client, today, baseline, [_item("a"), _item("transient")])
        _record(client, today, baseline, [_item("a")])

        rows = client.execute(
            "SELECT stable_id, status FROM daily_status "
            "WHERE archive_key='gov-support' AND snapshot_date=? ORDER BY stable_id",
            (today.isoformat(),),
        ).rows
        assert [tuple(row) for row in rows] == [("a", "ongoing")]
    finally:
        client.close()


def test_detail_enrichment_and_active_since_survive_list_only_upsert(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    try:
        day1 = date(2026, 8, 1)
        day2 = date(2026, 8, 2)
        enriched = _item(
            "a", summary="detail summary", region="부산", target="중소기업", amount="1억원",
        )
        _record(client, day1, None, [enriched])
        _record(client, day2, day1, [_item("a")])

        row = client.execute(
            "SELECT summary, region, target, amount, active_since FROM item WHERE stable_id='a'"
        ).rows[0]
        assert tuple(row) == (
            "detail summary", "부산", "중소기업", "1억원", day1.isoformat(),
        )
    finally:
        client.close()


def test_failed_run_is_not_a_mail_eligible_snapshot(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    try:
        today = date(2026, 8, 2)
        db.start_scrape_run(client, "gov-support", today, None)
        db.fail_scrape_run(client, "gov-support", today, "source unavailable")
        run = db.get_scrape_run(client, "gov-support", today)
        assert run is not None
        assert run["status"] == "failed"
        assert run["error"] == "source unavailable"
    finally:
        client.close()


def test_mail_delivery_marker_is_durable_and_replaceable(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    try:
        today = date(2026, 8, 2)
        assert db.get_mail_delivery(client, "gov-support", today) is None
        db.record_mail_delivery(client, "gov-support", today, "<first@example.com>")
        assert db.get_mail_delivery(client, "gov-support", today) == "<first@example.com>"

        db.record_mail_delivery(client, "gov-support", today, "<forced@example.com>")
        assert db.get_mail_delivery(client, "gov-support", today) == "<forced@example.com>"
    finally:
        client.close()
