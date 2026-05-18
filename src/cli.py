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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="biz-mailer")
    p.add_argument("--dry-run", action="store_true",
                   help="render but do not send mail or push archive repos")
    p.add_argument("--only", action="append", default=[],
                   choices=list(ARCHIVE_ORDER),
                   help="run only the named archive(s); may be passed multiple times")
    p.add_argument("--seed", action="store_true",
                   help="bootstrap state/*.json from current archive-repo HTML (PR2)")
    p.add_argument("--force", action="store_true",
                   help="ignore sent-marker and re-send today (PR4)")
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
    report = RunReport(date=today.date())
    for k in keys:
        report.results.append(_run_archive(ARCHIVES[k], today, args.dry_run))

    print(report.summary())

    if args.dry_run:
        # In dry-run, also dump HTML lengths so CI can confirm rendering ran.
        for r in report.results:
            print(f"  rendered html: {r.archive_key} = {len(r.html)} bytes")
    return 0 if not report.fatal_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
