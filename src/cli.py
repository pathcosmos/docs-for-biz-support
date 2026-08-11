"""Two-stage CLI for scrape persistence and publish/mail delivery."""

from __future__ import annotations

import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

from .config.archives import ARCHIVE_ORDER, ARCHIVES
from .models import ArchiveResult, RunReport

KST = ZoneInfo("Asia/Seoul")


def _run_scrape_stage(today: datetime, archive_keys: list[str], dry_run: bool) -> int:
    """Fetch each archive and persist an idempotent, complete daily snapshot."""
    from . import db, diff
    from .adapters import ADAPTERS

    today_d = today.date()
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
            baseline_d = db.get_latest_completed_snapshot_date(client, k, today_d)
            if not dry_run:
                db.start_scrape_run(client, k, today_d, baseline_d)
            try:
                adapter_result = adapter()
                if not adapter_result.usable:
                    failed_sources = [
                        r.source_key for r in adapter_result.source_reports if r.status == "failed"
                    ]
                    raise RuntimeError(
                        "sources failed without cache: " + ", ".join(failed_sources)
                    )
                items_today = adapter_result.items
                result = diff.classify(client, cfg, today_d, baseline_d, items_today)
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
                        source_reports=[r.as_dict() for r in adapter_result.source_reports],
                    )
                    print(f"  [OK]  {k}: scraped {len(items_today)} → "
                          f"new={len(new_ids)} ongoing={len(ongoing_ids)} "
                          f"expired={len(expired_ids)}  (DB updated)")
                for warning in adapter_result.warnings:
                    print(f"    [WARN] {warning}")
            except Exception as e:  # noqa: BLE001 — per-archive isolation
                any_failure = True
                if not dry_run:
                    db.fail_scrape_run(client, k, today_d, repr(e))
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

    """
    from . import db, state
    from .mailer.gmail_smtp import MailerError, send_html
    from .push.github_push import PushError, push_archive, wait_for_pages
    from .render.daily_html import render_daily_html

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
        any_failure = False
        for k in archive_keys:
            cfg = ARCHIVES[k]
            res = ArchiveResult(archive_key=k)
            try:
                durable_marker = db.get_mail_delivery(client, k, today_d)
                existing_marker = durable_marker or sent_marker.get(k)
                if existing_marker and not force and not dry_run:
                    # Keep both retry guards synchronized. More importantly,
                    # skip before publishing so an already-sent email's date
                    # URL cannot silently change without a matching resend.
                    if not durable_marker:
                        db.record_mail_delivery(client, k, today_d, existing_marker)
                    if k not in sent_marker:
                        state.mark_sent(today_d, k, existing_marker)
                    res.skipped_reason = f"already sent (marker={existing_marker})"
                    report.results.append(res)
                    continue
                scrape_run = db.get_scrape_run(client, k, today_d)
                if not scrape_run or scrape_run["status"] != "complete":
                    state_label = scrape_run["status"] if scrape_run else "missing"
                    res.skipped_reason = f"scrape snapshot is not complete ({state_label})"
                    res.scraper_errors.append(
                        (scrape_run.get("error") or f"scrape_run status={state_label}")
                        if scrape_run else "no scrape_run"
                    )
                    any_failure = True
                    report.results.append(res)
                    continue

                source_warnings = [
                    r.get("warning") for r in scrape_run.get("source_reports", [])
                    if r.get("warning")
                ]
                res.scraper_errors.extend(source_warnings)
                new_items, ongoing_items, expired_items = _load_today(client, k, today_d)
                res.items_new = new_items
                res.items_ongoing = ongoing_items
                res.items_expired = expired_items
                # If today has no DB rows (scrape didn't run / failed), nothing
                # to send. Skip rather than mailing an empty digest.
                if not (new_items or ongoing_items or expired_items):
                    res.skipped_reason = "no DB rows for today (scrape may have failed)"
                    any_failure = True
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
                    scraper_errors=source_warnings or None,
                    for_email=False,
                )
                res.html = render_daily_html(
                    cfg=cfg,
                    items_new=new_items,
                    items_ongoing=ongoing_items,
                    today=today_d,
                    items_expired=expired_items,
                    scraper_errors=source_warnings or None,
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
                    res.pushed = push_result.pushed or push_result.note in {
                        "no changes", "dry-run (commit prepared, push skipped)",
                    }
                    print(f"  [{k} push] {push_result.note} "
                          f"sha={push_result.committed_sha or '-'} "
                          f"files={push_result.files_changed}")
                    if not dry_run:
                        published_url = wait_for_pages(cfg, today_d)
                        res.pages_verified = True
                        print(f"  [{k} pages] verified {published_url}")
                except PushError as e:
                    res.scraper_errors.append(f"push failed: {e!s}")
                    res.skipped_reason = "push failed — mail aborted to avoid broken link"
                    any_failure = True
                    report.results.append(res)
                    continue

                if dry_run:
                    res.skipped_reason = "dry-run"
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
                        db.record_mail_delivery(client, k, today_d, marker_value)
                        state.mark_sent(today_d, k, marker_value)
                        res.mail_sent = True
                        print(f"  [{k} mail] sent to {len(mail_to)} to + {len(cc)} cc")
                    except MailerError as e:
                        res.scraper_errors.append(f"mail send failed: {e!s}")
                        any_failure = True
                        print(f"  [{k} mail] FAILED: {e!s}")
            except Exception as e:  # noqa: BLE001 — per-archive isolation
                res.scraper_errors.append(f"mail stage failure: {e!r}")
                any_failure = True
            report.results.append(res)
    finally:
        client.close()

    print(report.summary())
    if dry_run:
        for r in report.results:
            print(f"  rendered html: {r.archive_key} = {len(r.html)} bytes")
    return 1 if any_failure or report.fatal_errors else 0


def _load_today(client, archive_key: str, today_d):
    """Read today's items grouped by status. `new`/`ongoing` items come from
    the `item` table (today's scrape upserted them). `expired` items also
    live there — their last_seen is the prior snapshot because today's scrape didn't
    re-touch them."""
    new_items = _items_for_status(client, archive_key, today_d, "new")
    ongoing_items = _items_for_status(client, archive_key, today_d, "ongoing")
    expired_items = _items_for_status(client, archive_key, today_d, "expired")
    return new_items, ongoing_items, expired_items


def _run_backfill_bizinfo_api() -> int:
    """One-shot: enrich existing Bizinfo rows from the keyed official API.

    The historical command name is retained for workflow compatibility, but
    no DETAIL HTML pages are requested. One complete API response supplies
    summary, target, and hashtags for badge recalculation.
    """
    import json

    from . import db
    from .render.labels import assign_badges
    from .scrapers.base import HttpClient
    from .scrapers.bizinfo import fetch_listings as fetch_bizinfo

    print("== Backfill: keyed bizinfo API enrichment + GPU·AI labels ==")
    with HttpClient() as http:
        api_result = fetch_bizinfo(http)
    if api_result.channel != "api" or not api_result.complete:
        print("  ERROR: BIZINFO_API_KEY is required for API-only backfill")
        return 1

    api_by_stable_id = {
        f"bizinfo:{raw.pblanc_id}": raw for raw in api_result.items
    }
    client = db.connect()
    try:
        db.migrate(client)
        result = client.execute(
            "SELECT stable_id, title, organizer FROM item "
            "WHERE archive_key = 'gov-support' AND source_key = 'bizinfo'"
        )
        rows = list(result.rows)
        print(f"  bizinfo items in DB: {len(rows)}")

        ok = missing = labeled = 0
        for stable_id, stored_title, stored_organizer in rows:
            raw = api_by_stable_id.get(stable_id)
            if raw is None:
                missing += 1
                continue
            badges = assign_badges(
                title=raw.title or stored_title,
                summary=raw.summary,
                hashtags=list(raw.hashtags),
                organizer=raw.organizer or stored_organizer,
            )
            badges_json = json.dumps(list(badges), ensure_ascii=False) if badges else None
            db.backfill_item_enrichment(
                client,
                stable_id,
                badges_json=badges_json,
                summary=raw.summary,
                region=raw.region,
                target=raw.target,
            )
            ok += 1
            if badges:
                labeled += 1
        print(
            f"  DONE  db={len(rows)} api={len(api_result.items)} "
            f"updated={ok} missing={missing} labeled={labeled}"
        )
    finally:
        client.close()
    return 0


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
        "i.summary, i.badges_json, i.first_seen, i.active_since "
        "FROM daily_status d JOIN item i USING (stable_id) "
        "WHERE d.archive_key = ? AND d.snapshot_date = ? AND d.status = ? "
        "ORDER BY (i.deadline IS NULL), i.deadline, i.stable_id",
        (archive_key, today_d.isoformat(), status),
    )
    return [db._item_from_db_row(r) for r in result.rows]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="biz-mailer")
    stage = p.add_mutually_exclusive_group()
    stage.add_argument("--scrape", action="store_true",
                       help="scrape stage only: hit sources, diff, write today rows to DB. "
                            "The scheduled run is the scrape job in daily.yml.")
    stage.add_argument("--mail", action="store_true",
                       help="mail stage only: read today rows from DB, render, push 5 archive "
                            "repos, verify Pages, and send 5 emails.")
    stage.add_argument("--seed", action="store_true",
                       help="bootstrap Turso item/daily_status from current archive-repo HTML")
    stage.add_argument("--backfill-bizinfo-api", "--backfill-details",
                       dest="backfill_bizinfo_api", action="store_true",
                       help="one-shot: use the keyed Bizinfo API to refresh summary, target, "
                            "hashtags, and GPU·AI labels for existing rows")

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

    if args.backfill_bizinfo_api:
        return _run_backfill_bizinfo_api()

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
