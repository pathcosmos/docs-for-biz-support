"""CLI entrypoint. PR1 supports --dry-run end-to-end with a stubbed adapter
that emits a tiny placeholder Item set per archive, so the renderer wiring and
the GH Actions workflow can be exercised before real scrapers exist."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from .config.archives import ARCHIVE_ORDER, ARCHIVES, ArchiveConfig
from .models import ArchiveResult, Item, RunReport
from .render.daily_html import render_daily_html


KST = ZoneInfo("Asia/Seoul")


def _placeholder_items(cfg: ArchiveConfig) -> tuple[list[Item], list[Item]]:
    """Until real scrapers/adapters land, every archive renders one demo new
    item and two ongoing items so the layout can be eyeballed."""
    src = cfg.sources[0]
    cat = cfg.categories[0].key if cfg.categories else None
    new = [
        Item(
            stable_id="demo-new-1",
            source_key=src,
            title="[샘플] 새로 공고된 지원사업",
            detail_url="https://example.com/announce/1",
            category=cat,
            organizer="샘플 주관기관",
            apply_period="2026-05-18 ~ 2026-05-25",
            region="전국",
            target="중소기업",
            summary="이 항목은 PR1 스켈레톤의 렌더링 검증용 더미입니다.",
        ),
    ]
    ongoing = [
        Item(
            stable_id=f"demo-ongoing-{i}",
            source_key=src,
            title=f"[샘플] 진행 중 지원사업 {i}",
            detail_url=f"https://example.com/announce/ongoing-{i}",
            category=cat,
            organizer="샘플 주관기관",
            apply_period="2026-05-01 ~ 2026-06-30",
            region="전국",
            target="중소기업",
        )
        for i in (1, 2)
    ]
    return new, ongoing


def _run_archive(cfg: ArchiveConfig, today: datetime, dry_run: bool) -> ArchiveResult:
    res = ArchiveResult(archive_key=cfg.key)
    try:
        new, ongoing = _placeholder_items(cfg)
        res.items_new = new
        res.items_ongoing = ongoing
        res.html = render_daily_html(
            cfg, new, ongoing, today.date(),
            scraper_errors=res.scraper_errors or None,
        )
        if dry_run:
            res.skipped_reason = "dry-run"
            return res
        # mail + push wiring is added in later PRs
        res.skipped_reason = "PR1 stub — mail/push not implemented yet"
    except Exception as e:  # noqa: BLE001 — per-archive isolation
        res.scraper_errors.append(f"orchestrator failure: {e!r}")
    return res


def _run_scrape_stage(today: datetime, archive_keys: list[str], dry_run: bool) -> int:
    """Per archive: invoke the registered adapter to fetch today's items,
    diff against yesterday's DB snapshot, write the three statuses to
    daily_status (idempotent on re-run), and prune anything past the 100-day
    retention. Archives without a registered adapter are skipped with a note —
    real scrapers land in PR4a/4b/8 incrementally."""
    from datetime import timedelta
    from . import db, diff
    from .adapters import ADAPTERS

    today_d = today.date()
    yesterday_d = today_d - timedelta(days=1)

    print(f"== Scrape stage {today_d.isoformat()} ==")
    client = db.connect()
    try:
        db.migrate(client)
        any_failure = False
        for k in archive_keys:
            cfg = ARCHIVES[k]
            adapter = ADAPTERS.get(k)
            if adapter is None:
                print(f"  [SKIP] {k}: no adapter registered yet")
                continue
            try:
                items_today = adapter()
                result = diff.classify(client, cfg, today_d, yesterday_d, items_today)
                new_ids = {i.stable_id for i in result.new}
                ongoing_ids = {i.stable_id for i in result.ongoing}
                expired_ids = {i.stable_id for i in result.expired}
                if dry_run:
                    print(f"  [DRY] {k}: scraped {len(items_today)} → "
                          f"new={len(new_ids)} ongoing={len(ongoing_ids)} "
                          f"expired={len(expired_ids)}  (no DB write)")
                else:
                    db.record_daily(
                        client, k, today_d, items_today,
                        new_ids=new_ids, ongoing_ids=ongoing_ids, expired_ids=expired_ids,
                    )
                    print(f"  [OK]  {k}: scraped {len(items_today)} → "
                          f"new={len(new_ids)} ongoing={len(ongoing_ids)} "
                          f"expired={len(expired_ids)}  (DB updated)")
            except Exception as e:  # noqa: BLE001 — per-archive isolation
                any_failure = True
                import traceback
                print(f"  [FAIL] {k}: {e!r}")
                # Brief traceback (3 frames) so production failures are
                # diagnosable from the Actions log without re-running locally.
                tb = traceback.format_exc().splitlines()
                for line in tb[-12:]:
                    print(f"    | {line}")
        # Retention sweep — only when we actually wrote something
        if not dry_run:
            d_del, i_del = db.prune(client, today_d)
            print(f"  prune: removed {d_del} daily_status rows, {i_del} orphan items")
    finally:
        client.close()
    return 1 if any_failure else 0


def _run_mail_stage(today: datetime, archive_keys: list[str], dry_run: bool,
                    force: bool) -> int:
    """Per archive: read today's daily_status from DB, render the HTML, push
    the day-file + archive.json + index.html to the archive repo, then send
    the mail. **Push happens BEFORE mail** so the in-mail "전체 보기" link is
    live by the time the mail is opened.

    Mail send is still PR7 (currently skipped). Push (PR6) is wired here.
    """
    import os
    from . import db, state
    from .render.daily_html import render_daily_html
    from .push.github_push import push_archive, PushError
    from .mailer.gmail_smtp import send_html, MailerError

    today_d = today.date()
    print(f"== Mail stage {today_d.isoformat()} ==")
    sent_marker = state.load_sent_marker(today_d)
    if sent_marker and not force:
        print(f"  marker found for {today_d.isoformat()}: "
              f"{list(sent_marker.keys())} already sent — pass --force to re-send")

    mail_to = _csv_env("MAIL_TO")
    report = RunReport(date=today_d)
    client = db.connect()
    try:
        db.migrate(client)
        for k in archive_keys:
            cfg = ARCHIVES[k]
            res = ArchiveResult(archive_key=k)
            try:
                new_items, ongoing_items, expired_items = _load_today(client, k, today_d)
                res.items_new = new_items
                res.items_ongoing = ongoing_items
                # If today has no DB rows (scrape didn't run / failed), nothing
                # to send. Skip rather than mailing an empty digest.
                if not (new_items or ongoing_items or expired_items):
                    res.skipped_reason = "no DB rows for today (scrape may have failed)"
                    report.results.append(res)
                    continue

                # Two renderings: archive copy has no clip-banner, mail has it.
                # Identical content otherwise.
                html_archive = render_daily_html(
                    cfg=cfg,
                    items_new=new_items,
                    items_ongoing=ongoing_items,
                    today=today_d,
                    items_expired=expired_items,
                    for_email=False,
                )
                res.html = render_daily_html(
                    cfg=cfg,
                    items_new=new_items,
                    items_ongoing=ongoing_items,
                    today=today_d,
                    items_expired=expired_items,
                    for_email=True,
                )

                # PUSH FIRST — the email's clip-banner link points at the
                # archive copy. If push fails, we skip mail to avoid a 404 link.
                try:
                    push_result = push_archive(
                        cfg=cfg, today=today_d,
                        daily_html_for_archive=html_archive,
                        new_count=len(new_items),
                        ongoing_count=len(ongoing_items),
                        expired_count=len(expired_items),
                        dry_run=dry_run,
                    )
                    res.pushed = push_result.pushed or push_result.note == "dry-run (commit prepared, push skipped)"
                    print(f"  [{k} push] {push_result.note} "
                          f"sha={push_result.committed_sha or '-'} "
                          f"files={push_result.files_changed}")
                except PushError as e:
                    res.scraper_errors.append(f"push failed: {e!s}")
                    res.skipped_reason = "push failed — mail aborted to avoid broken link"
                    report.results.append(res)
                    continue

                if dry_run:
                    res.skipped_reason = "dry-run"
                elif k in sent_marker and not force:
                    res.skipped_reason = f"already sent (marker={sent_marker[k]})"
                else:
                    subject = _build_subject(
                        cfg.subject_prefix, today_d,
                        len(new_items), len(ongoing_items), len(expired_items),
                    )
                    cc = _csv_env(cfg.cc_env) if cfg.cc_env else []
                    try:
                        marker_value = send_html(
                            subject=subject,
                            html=res.html,
                            to=mail_to,
                            cc=cc,
                        )
                        state.mark_sent(today_d, k, marker_value)
                        res.mail_sent = True
                        print(f"  [{k} mail] sent to {len(mail_to)} to + {len(cc)} cc")
                    except MailerError as e:
                        res.scraper_errors.append(f"mail send failed: {e!s}")
                        print(f"  [{k} mail] FAILED: {e!s}")
            except Exception as e:  # noqa: BLE001 — per-archive isolation
                res.scraper_errors.append(f"mail stage failure: {e!r}")
            report.results.append(res)
    finally:
        client.close()

    print(report.summary())
    if dry_run:
        for r in report.results:
            print(f"  rendered html: {r.archive_key} = {len(r.html)} bytes")
    return 0 if not report.fatal_errors else 1


def _load_today(client, archive_key: str, today_d):
    """Read today's items grouped by status. `new`/`ongoing` items come from
    the `item` table (today's scrape upserted them). `expired` items also
    live there — their last_seen is yesterday because today's scrape didn't
    re-touch them."""
    from . import db
    new_items = _items_for_status(client, archive_key, today_d, "new")
    ongoing_items = _items_for_status(client, archive_key, today_d, "ongoing")
    expired_items = _items_for_status(client, archive_key, today_d, "expired")
    return new_items, ongoing_items, expired_items


def _csv_env(name: str) -> list[str]:
    """Parse a comma-separated env var into a list of trimmed, non-empty strings."""
    import os
    raw = os.environ.get(name, "")
    return [s.strip() for s in raw.split(",") if s.strip()]


def _build_subject(prefix: str, today_d, new_n: int, ongoing_n: int, expired_n: int) -> str:
    """Compose the mail subject line. Matches the format the user provided as
    the historical reference: `<prefix> 신규 N건 · 진행중 N건 (YYYY-MM-DD)`,
    with optional `· 종료 N건` segment when expired_n > 0."""
    parts = [f"신규 {new_n}건", f"진행중 {ongoing_n}건"]
    if expired_n:
        parts.append(f"종료 {expired_n}건")
    return f"{prefix} {' · '.join(parts)} ({today_d.isoformat()})"


def _items_for_status(client, archive_key: str, today_d, status: str):
    from . import db
    result = client.execute(
        "SELECT i.stable_id, i.source_key, i.title, i.detail_url, i.category, "
        "i.organizer, i.amount, i.apply_period, i.deadline, i.region, i.target, "
        "i.summary, i.badges_json "
        "FROM daily_status d JOIN item i USING (stable_id) "
        "WHERE d.archive_key = ? AND d.snapshot_date = ? AND d.status = ?",
        (archive_key, today_d.isoformat(), status),
    )
    return [db._item_from_db_row(r) for r in result.rows]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="biz-mailer")
    stage = p.add_mutually_exclusive_group()
    stage.add_argument("--scrape", action="store_true",
                       help="scrape stage only: hit sources, diff, write today rows to DB. "
                            "Run from scrape.yml at 07:30 KST.")
    stage.add_argument("--mail", action="store_true",
                       help="mail stage only: read today rows from DB, render, push 5 archive "
                            "repos, send 5 emails. Run from mail.yml at 08:30 KST.")
    stage.add_argument("--seed", action="store_true",
                       help="bootstrap Turso item/daily_status from current archive-repo HTML")

    p.add_argument("--dry-run", action="store_true",
                   help="render but do not send mail / push archive repos / write DB")
    p.add_argument("--only", action="append", default=[],
                   choices=list(ARCHIVE_ORDER),
                   help="run only the named archive(s); may be passed multiple times")
    p.add_argument("--force", action="store_true",
                   help="ignore sent-marker and re-send today (mail stage only)")
    p.add_argument("--date", default=None,
                   help="override 'today' as YYYY-MM-DD (KST); default = now KST")
    args = p.parse_args(argv)

    if args.seed:
        from .seed.bootstrap import seed_all
        results = seed_all()
        for k, r in results.items():
            print(f"  {k}: {len(r.items)} items (from {r.snapshot_date}, "
                  f"{r.skipped_cards} skipped)")
            for w in r.warnings:
                print(f"    ⚠ {w}")
        return 0

    if args.date:
        today = datetime.fromisoformat(args.date).replace(tzinfo=KST)
    else:
        today = datetime.now(KST)

    keys = args.only or list(ARCHIVE_ORDER)

    if args.scrape:
        return _run_scrape_stage(today, keys, args.dry_run)
    if args.mail:
        return _run_mail_stage(today, keys, args.dry_run, args.force)

    # No stage selected → run both (manual dev). Sequence matters: scrape
    # populates today's DB rows that mail consumes.
    rc = _run_scrape_stage(today, keys, args.dry_run)
    if rc != 0:
        return rc
    return _run_mail_stage(today, keys, args.dry_run, args.force)


if __name__ == "__main__":
    raise SystemExit(main())
