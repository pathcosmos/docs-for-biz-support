"""Smoke test for PR3 — diff classification, expired section, archive_json,
index_html.

Strategy: use the DB seeded by `--seed` (containing 2026-05-13 items as
'ongoing'). Synthesize a fake "today (2026-05-18)" scrape by:
- Dropping the first 3 items from each archive → they become expired
- Adding 2 fake items (with stable_id 'pr3-test-X') → they become new
- Keeping the rest → ongoing

Run diff.classify, then render the daily HTML, archive.json, and index.html
for one archive (kstartup-biz — small enough to eyeball). Output goes to
mails-pr3/ so it doesn't clobber the existing mails/ preview."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import db, diff  # type: ignore
from src.config.archives import ARCHIVES
from src.models import Item
from src.render.archive_json import update_archive_json
from src.render.daily_html import render_daily_html
from src.render.index_html import render_index_html


def _load_seeded_items(client, archive_key: str, seed_date: date) -> list[Item]:
    """Pull the seeded items (status='ongoing' on seed_date) back as a list."""
    result = client.execute(
        "SELECT i.stable_id, i.source_key, i.title, i.detail_url, i.category, "
        "i.organizer, i.amount, i.apply_period, i.deadline, i.region, i.target, "
        "i.summary, i.badges_json "
        "FROM daily_status d JOIN item i USING (stable_id) "
        "WHERE d.archive_key = ? AND d.snapshot_date = ?",
        (archive_key, seed_date.isoformat()),
    )
    return [db._item_from_db_row(r) for r in result.rows]


def _synthesize_today(seeded: list[Item], cfg, today: date) -> list[Item]:
    """Drop the first 3 (will become expired) and append 2 new fake items."""
    survived = list(seeded[3:])
    fake_src = cfg.sources[0]
    fake_cat = cfg.categories[0].key if cfg.categories else None
    new_fakes = [
        Item(
            stable_id=f"pr3-test-new-{i}",
            source_key=fake_src,
            title=f"[PR3-TEST] 신규 항목 {i} — 디프 검증",
            detail_url=f"https://example.com/pr3-test/{i}",
            category=fake_cat,
            organizer="PR3 검증 주관기관",
            apply_period=f"{today.isoformat()} ~ 2026-06-01",
            deadline=date(2026, 6, 1),
            region="전국",
            target="중소기업",
            summary=f"PR3 smoke test의 신규 분류 검증용 더미 항목 {i}입니다.",
        )
        for i in (1, 2)
    ]
    return survived + new_fakes


def main() -> int:
    archive_key = "kstartup-biz"
    cfg = ARCHIVES[archive_key]

    client = db.connect()
    try:
        db.migrate(client)
        # Find the actual most-recent snapshot for this archive in the DB —
        # seed populates whatever the archive repo's latest YYYY-MM-DD.html is,
        # which moves whenever PR6 push lands a fresh day.
        latest = client.execute(
            "SELECT MAX(snapshot_date) FROM daily_status WHERE archive_key = ?",
            (archive_key,),
        )
        seed_date_str = latest.rows[0][0] if latest.rows else None
        if not seed_date_str:
            print(f"no seed data for {archive_key} — run `python -m src.cli --seed` first")
            return 1
        yesterday = date.fromisoformat(seed_date_str)
        today = date.fromordinal(yesterday.toordinal() + 5)  # +5 days for a clean test horizon
        print(f"using yesterday={yesterday} (seeded), today={today}")
        seeded = _load_seeded_items(client, archive_key, yesterday)
        print(f"seeded ({yesterday}): {len(seeded)} items for {archive_key}")

        items_today = _synthesize_today(seeded, cfg, today)
        print(f"synthetic today ({today}): {len(items_today)} items "
              f"(= {len(seeded)} - 3 expired + 2 new)")

        result = diff.classify(client, cfg, today, yesterday, items_today)
        new_n, ongoing_n, expired_n = result.counts
        print(f"diff: new={new_n}  ongoing={ongoing_n}  expired={expired_n}")
        assert new_n == 2, f"expected 2 new, got {new_n}"
        assert expired_n == 3, f"expected 3 expired, got {expired_n}"
        assert ongoing_n == len(seeded) - 3, \
            f"expected {len(seeded)-3} ongoing, got {ongoing_n}"

        # Render the daily HTML with all three sections.
        html = render_daily_html(
            cfg=cfg,
            items_new=result.new,
            items_ongoing=result.ongoing,
            today=today,
            items_expired=result.expired,
            for_email=True,
        )
        out_dir = Path(__file__).resolve().parents[1] / "mails-pr3" / archive_key
        out_dir.mkdir(parents=True, exist_ok=True)
        out_html = out_dir / f"{today.isoformat()}.html"
        out_html.write_text(html, encoding="utf-8")
        print(f"daily HTML → {out_html} ({len(html):,} bytes)")
        assert "🆕 신규 (2건)" in html, "missing 신규 section"
        assert "🔚 오늘 종료 (3건)" in html, "missing 종료 section"
        assert f"진행중 {ongoing_n}건" in html, "missing 진행중 in header"
        assert f"종료 {expired_n}건" in html, "missing 종료 count in header"

        # archive_json update — start from empty and from existing.
        empty_aj = update_archive_json(
            existing_content=None, title=cfg.title, today=today,
            new_count=new_n, ongoing_count=ongoing_n, expired_count=expired_n,
        )
        existing_yesterday_aj = (
            '{"entries":[{"date":"2026-05-13","new_count":0,"ongoing_count":126}],'
            '"title":"💼 K-Startup 사업화"}'
        )
        merged_aj = update_archive_json(
            existing_content=existing_yesterday_aj, title=cfg.title, today=today,
            new_count=new_n, ongoing_count=ongoing_n, expired_count=expired_n,
        )
        assert '"expired_count": 3' in merged_aj, "expired_count missing from new entry"
        assert '"date": "2026-05-13"' in merged_aj, "old entry was lost"
        # And re-running for the same day should replace, not duplicate.
        re_run = update_archive_json(
            existing_content=merged_aj, title=cfg.title, today=today,
            new_count=99, ongoing_count=99, expired_count=99,
        )
        today_str = f'"date": "{today.isoformat()}"'
        assert re_run.count(today_str) == 1, f"same-day duplicate on {today.isoformat()}"
        assert '"new_count": 99' in re_run, "re-run did not replace today"

        out_aj = out_dir.parent / "archive.json"
        out_aj.write_text(merged_aj, encoding="utf-8")
        print(f"archive.json → {out_aj} ({len(merged_aj)} bytes)")

        # index_html.
        index_html = render_index_html(cfg, merged_aj)
        out_idx = out_dir.parent / "index.html"
        out_idx.write_text(index_html, encoding="utf-8")
        print(f"index.html  → {out_idx} ({len(index_html)} bytes)")
        assert "종료 3건" in index_html, "expired count missing from index row"
        assert f"refresh\" content=\"3; url=./{today.isoformat()}.html\"" in index_html, \
            "meta-refresh wrong"
    finally:
        client.close()

    print("\n✓ PR3 smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
