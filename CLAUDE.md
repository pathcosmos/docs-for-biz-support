# Repository Guidance

This is a production Python mailer, not a greenfield scaffold. Read `README.md`
before changing the daily pipeline.

## Non-negotiable invariants

- The scheduled workflow is `.github/workflows/daily.yml`: scrape must succeed
  before publish/mail starts.
- Mail may consume only a `scrape_run.status = complete` snapshot for the same
  KST date.
- Diff against the latest earlier completed snapshot, not calendar yesterday.
- A partial or failed source must use that source's last completed cached slice.
  If neither fresh data nor a cache exists, fail the archive.
- Push the date archive, verify the GitHub Pages date URL, then send mail.
- Record successful delivery in Turso `mail_delivery` before writing the Git
  audit marker.
- Preserve detail enrichment when later list-only scrapes omit those fields.
- Re-running a scrape for the same date must replace that date's status rows.

## Government mail priority

An item that is both newly active and matches the priority conditions belongs
in `신규 + 우선조건 동시충족` for active-cycle days D+0 through D+6. Starting
at D+7 it belongs in `우선조건 충족 — 부산·경남·경북 · 제조·AI · 중견·중소`.
Use `active_since`, not the original lifetime `first_seen`, so reappearing items
start a new active cycle.

## Verification

Run all three before finishing:

```bash
uv run --frozen ruff check src tests
uv run --frozen pytest -q
uv run --frozen python -m compileall -q src tests
```
