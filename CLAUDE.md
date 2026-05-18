# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

**Greenfield.** No source code exists yet. The repo currently holds only this CLAUDE.md. Everything below describes the system that needs to be built, derived by reverse-engineering the five output archive repos under the `pathcosmos` GitHub org.

## What this project is

A daily (08:30 KST) cron job that:

1. **Scrapes** five distinct Korean government-support / startup-support data sources.
2. **Diffs** today's listings against the previous day's snapshot to classify each item as `new` or `ongoing`.
3. **Renders** five separate inline-styled HTML emails (one per source).
4. **Sends** five separate emails via Gmail SMTP using `lanco.gh@gmail.com` + an app password (5 distinct mails, not a single combined digest — confirmed requirement). Resend was rejected because the user requires the `From:` address to be `lanco.gh@gmail.com`, which Resend (and any third-party MTA) cannot send from — only Google controls `gmail.com`'s DKIM.
5. **Archives** each day's HTML to its corresponding GitHub Pages repo as `YYYY-MM-DD.html`, updates `archive.json` and `index.html`, and commits/pushes.

Runtime is **GitHub Actions** (decided). Mail provider is **Gmail SMTP** with an app password (decided). Resend was considered and ruled out because of the From-address constraint above — don't re-litigate without asking.

## The five archive repos (output contract)

Each is a separate public repo under `pathcosmos`. This project must push to all five on every successful run. The daily HTML pushed to each repo is also the exact body sent in the corresponding email.

| Repo | Email subject / `<h1>` | Daily-HTML header color | Email-footer bot name | Sources scraped |
|------|------------------------|-------------------------|-----------------------|-----------------|
| `gov-support-archive` | 🏛️ 정부지원사업 모니터 | `#1a73e8` | `Gov Support Bot \| 자동발송` | 기업마당 (bizinfo.go.kr), 중소벤처24 (smes.go.kr), IRIS 범부처R&D (iris.go.kr), 테크노파크들 (gbtp, jntp, btp, …), 스마트공장 — multi-source aggregator |
| `busan-startup-archive` | 🚀 부산창업 서비스 | `#0d47a1` | `Busan Startup Bot \| 자동발송` | 부산창업지원 (부산기술창업투자원 pms.ripc.org, busanstartup.kr, 부산창조경제혁신센터, …). Items grouped into category sub-sections (e.g. `🧭 멘토링·컨설팅`, `🏢 시설/공간` rendered with darker `#37474f` border) |
| `kstartup-biz-archive` | 💼 K-Startup 사업화 | `#1a73e8` | `K-Startup Bot \| 자동발송` | `k-startup.go.kr/web/contents/webCMRCZN.do` (사업화 listings) |
| `kstartup-mentoring-archive` | 🧭 K-Startup 멘토링 · R&D | `#00897b` | `K-Startup Bot \| 자동발송` | `k-startup.go.kr/web/contents/webRND.do`. Grouped by category (`🔬 R&D` purple `#7b1fa2`, `🧭 멘토링`, …) |
| `kstartup-global-archive` | 🌏 K-Startup 글로벌 · 시설 | `#0277bd` | `K-Startup Bot \| 자동발송` | `k-startup.go.kr/web/contents/webFC_SP_NR.do`. Grouped by category (`🏢 시설/공간` deep-purple `#4527a0`, `🌏 글로벌`, …) |

Note the three K-Startup archives all share `K-Startup Bot | 자동발송` as the footer — it identifies the bot family, not the archive. Don't "fix" that to per-archive bot names without checking with the user.

Each archive repo follows the same file layout:

- `YYYY-MM-DD.html` — one per day, the rendered email body. Self-contained: inline CSS only, no external assets. Korean-language, max-width 680px. Footer is a single centered line: `<archive-bot-name>`. **Currently contains no back-link to the archive index — see "Archive-index linking" below for the recommended addition.**
- `archive.json` — schema:
  ```json
  {
    "entries": [
      {"date": "YYYY-MM-DD", "new_count": N, "ongoing_count": N}
    ],
    "title": "🏛️ 정부지원사업 모니터"
  }
  ```
  The top-level `title` field stores the archive's display name (emoji + label, matching the daily-HTML `<h1>`). Newest entry appended each day. Pre-existing `entries` are preserved.
- `index.html` — landing page. Structure:
  - `<title>{emoji + title} — Archive</title>` (e.g. `🏛️ 정부지원사업 모니터 — Archive`)
  - `<meta http-equiv="refresh" content="3; url=./YYYY-MM-DD.html">` pointing at the newest date
  - A hero box. **All five archives use `#1a73e8` for the hero background** — this is uniform across archives and intentionally does NOT match the per-archive daily-HTML header color. Don't change it without checking.
  - Hero copy: `3초 뒤 최신 아카이브(YYYY-MM-DD)로 이동합니다. <a>바로 열기</a>`
  - Heading: `📁 전체 아카이브 (N일)` where N is `len(entries)`
  - Table of all dates (newest first), each row: `<a href="./YYYY-MM-DD.html">YYYY-MM-DD</a>` + `신규 N건 · 진행중 N건`
  - Footer link: `<a href="https://github.com/pathcosmos/<repo>">GitHub Repo</a>` in muted `#bbb`

  Must be fully regenerated every run from `archive.json`.
- `README.md` — single short stanza, format:
  ```
  # <repo-name>

  정부지원사업 메일링 아카이브. 매일 08:30 KST 자동 업데이트.

  - GitHub Pages: https://pathcosmos.github.io/<repo-name>/
  - 날짜별: `YYYY-MM-DD.html`
  ```

### Canonical GitHub Pages URLs

These are the URLs to embed in emails for "see the archive" / "see past dates" links:

- https://pathcosmos.github.io/gov-support-archive/
- https://pathcosmos.github.io/busan-startup-archive/
- https://pathcosmos.github.io/kstartup-biz-archive/
- https://pathcosmos.github.io/kstartup-mentoring-archive/
- https://pathcosmos.github.io/kstartup-global-archive/

Each URL serves the latest day after 3-second auto-redirect; users who click "stop redirect" land on the full date table.

## Archive-index linking (gap in the current footer)

The existing daily HTMLs end with `<archive-bot-name> | 자동발송` and nothing else — no link to the archive index, no cross-archive navigation, no GitHub repo link in the email body. For the rebuild, the footer should be enriched. **Recommended footer block** (place above the existing `<bot-name> | 자동발송` line, keep that line as the bottom signature):

```
🗂️ 지난 아카이브 보기 → <a href="https://pathcosmos.github.io/<this-archive>/">전체 보기</a>

📬 다른 메일링:
  🏛️ <a href="https://pathcosmos.github.io/gov-support-archive/">정부지원사업</a> ·
  🚀 <a href="https://pathcosmos.github.io/busan-startup-archive/">부산창업</a> ·
  💼 <a href="https://pathcosmos.github.io/kstartup-biz-archive/">K-Startup 사업화</a> ·
  🧭 <a href="https://pathcosmos.github.io/kstartup-mentoring-archive/">멘토링·R&D</a> ·
  🌏 <a href="https://pathcosmos.github.io/kstartup-global-archive/">글로벌·시설</a>
```

In the "다른 메일링" row, omit the current archive's own entry to avoid a self-link. The first link ("전체 보기") is the recipient's primary path to past-date browsing; it should always be present.

There is currently NO top-level `pathcosmos.github.io` org page that aggregates all five archives — only the five per-archive Pages exist. If a unified org landing page is desired later, that's a separate `pathcosmos/pathcosmos.github.io` repo to create; do not bake assumptions about it into the daily HTML.

## Top-of-mail "view in browser" banner

Gmail's web/mobile inbox renderer truncates messages past ~102 KB with `[Message clipped]`. The `gov-support` daily can reach 600+ KB on busy days. To give recipients a one-click escape, every email body starts with a banner pointing at the day-specific archive copy:

```
📄 메일이 길어 잘렸나요? 브라우저에서 전체 보기 → <pages_url>YYYY-MM-DD.html
```

The URL is the **specific date** (`<pages_url>YYYY-MM-DD.html`), not the index — that way old emails keep pointing at the right day even after newer days have been archived.

The banner is rendered by `_clip_banner()` in `src/render/daily_html.py` and is gated by the `for_email: bool = True` parameter of `render_daily_html()`. When the same renderer is used to produce the file pushed to the archive repo, pass `for_email=False` so the in-browser view doesn't have a redundant "view in browser" link pointing at the page the reader is already on.

## Ordering: push first, then mail

This is a hard rule because the "view in browser" banner links into the archive repo. If the email arrives before the push lands, that link 404s for the seconds-to-minutes window before the push completes.

Required orchestration order per archive:

1. Scrape + diff
2. Render HTML (one call with `for_email=False` for the archive push; another with `for_email=True` for the mail body — they differ only in the top banner)
3. **Push to archive repo first** (clone → drop files → commit → push)
4. Wait briefly for GitHub Pages to publish (typically <30 s; in practice the file is immediately accessible via the `pathcosmos.github.io` URL after the commit lands)
5. **Then send email**

If push fails for an archive, do NOT send that archive's email — the "view in browser" link would be broken and so would yesterday's mail's "다른 메일링" cross-link to today's content if recipients reload. Record the failure in `RunReport.push_errors`; the other four archives continue normally.

## Daily-HTML rendering contract

Every daily HTML follows the same structure; only the header color/emoji/title and the category-grouping logic vary per archive.

```
<body> [Apple SD Gothic Neo / 맑은 고딕, max-width:680px]
  <div header-bar [archive-specific color]>
    <h1>{emoji + title}</h1>
    <p>{YYYY-MM-DD} | 신규 {new_count}건 · 진행중 {ongoing_count}건</p>
  </div>
  <div content-wrapper>
    {if new_count > 0:}
      <div section-header [#1a73e8]><h2>🆕 신규 ({new_count}건)</h2></div>
      {for each new item: <item-card border-left:4px solid {color}>}
    {if ongoing_count > 0:}
      <div section-header [#5f6368]><h2>📋 진행 중 ({ongoing_count}건)</h2></div>
      {if archive has categories: group items under sub-headers like 🔬 R&D, 🧭 멘토링·컨설팅, etc.}
      {for each ongoing item: <item-card>}
```

**Item card** fields (only emit rows that have data):

- 📌 `<a href="{detail_url}">{title}</a>` plus optional pill badges (e.g. `🖥️ GPU/클라우드`)
- Source tag pill (e.g. `중소벤처24`, `기업마당`, `K-Startup 사업소개`, `부산창업지원`, `gbtp`, `jntp`, `IRIS 범부처R&D`)
- Two-column table with any subset of: 🏢 주관기관, 💰 지원금액, 📅 신청기간, 🗺️ 지역, 👥 지원대상
- Deadline urgency badge inside 신청기간 cell:
  - `🔥 D-N` red (`#ea4335`) when N ≤ 5
  - `⚠️ D-N` orange (`#f57c00`) when 6 ≤ N ≤ 14
  - no badge otherwise
- Optional `<p>` summary paragraph (truncated, ~200 chars)

The exact inline CSS used in the existing archives is the source of truth — read any current `YYYY-MM-DD.html` from any of the five repos via `gh api repos/pathcosmos/<repo>/contents/<date>.html --jq .content | base64 -d` and match the styling byte-for-byte. Email clients are picky; do not refactor to external CSS / `<style>` blocks unless deliberately tested across Gmail / Outlook / Apple Mail.

## new vs ongoing classification

`new` = item was not present in yesterday's snapshot.
`ongoing` = item was present yesterday and still present today, and (where applicable) the application period hasn't closed.

Stable identity for diffing should be the detail-page URL or its unique ID parameter (e.g. `pblancId`, `NTTSN`, `id=` in K-Startup URLs, `ancmId` in IRIS) — not the title, since titles get edited. Persist yesterday's snapshot somewhere durable; the simplest path is to read it out of the archive repo's previous `YYYY-MM-DD.html` (or a sidecar `YYYY-MM-DD.json` if you decide to add one — currently no JSON snapshot exists, only the rendered HTML + `archive.json` counts).

## Repo layout

```
.github/workflows/daily.yml      # cron: '30 23 * * *' (23:30 UTC = 08:30 KST next day)
pyproject.toml
src/
  config/
    archives.py                  # ARCHIVES registry — 5 ArchiveConfig entries
    categories.py                # per-archive category emoji/color tables
    sources.py                   # source_key → display name, stable-id rule
  models.py                      # Item, ArchiveResult, RunReport dataclasses
  scrapers/                      # raw HTTP + HTML parse → RawRecord[]
    base.py  bizinfo.py  smes.py  iris.py  ntis.py  smart_factory.py
    technopark.py                # cbtp/djtp/gbtp/jntp/utp/btp parametrized
    busan_startup.py  ripc.py  bccei.py
    kstartup.py                  # one client, 3 endpoints (CMRCZN/RND/FC_SP_NR)
  adapters/                      # RawRecord → Item (sets source_key + category)
    gov_support.py  busan_startup.py
    kstartup_biz.py  kstartup_mentoring.py  kstartup_global.py
  diff.py                        # new/ongoing classification
  state.py                       # state/*.json load/save + sent-marker
  render/
    daily_html.py  index_html.py  archive_json.py  footer.py
  mailer/gmail_smtp.py           # smtplib SSL + app password + To/Cc
  push/github_push.py            # clone + commit + push per archive repo
  seed/bootstrap.py              # Day-1: parse existing archive HTML → state
  orchestrator.py                # run_archive(cfg) → ArchiveResult; main()
  cli.py                         # --dry-run --only --date --seed --force
state/                           # committed back to THIS repo
  gov-support.json  busan-startup.json  …  sent-YYYY-MM-DD.marker
```

Locked: **Python 3.11+** with httpx + BeautifulSoup4 + jinja2 + lxml. Don't switch to Node without explicit user direction — Korean text handling and the GH Actions Python action both work better here.

## GitHub Actions shape

- One workflow `daily.yml` on cron + `workflow_dispatch`.
- Single job that loops over the five archives. Each archive failure should NOT abort the others — wrap each in its own step or catch and report at the end. Daily mail must not silently fail.
- Secrets required:
  - `GMAIL_USER` — `lanco.gh@gmail.com`
  - `GMAIL_APP_PASSWORD` — 16-char Google app password (2FA-enabled account). Rotate immediately if ever shared outside the secrets store.
  - `MAIL_TO` — `lanco.gh@gmail.com`. All five archives share this `To:` address (only the `Cc:` list differs).
  - `MAIL_CC_GOV_SUPPORT` — comma-separated: `leeji@dkpia.com,dhkim1739@dkpia.com,yg.kim@dkpia.com,ghong@dkpia.com,kj9016@dkpia.com,chlee@dkpia.com`. The other four archives have no CC.
  - `ARCHIVE_PUSH_TOKEN` — fine-grained PAT scoped to the five archive repos with `contents:write`. The default `GITHUB_TOKEN` only has access to *this* repo, not the five archive repos.
- The push step must clone each archive repo into a scratch dir, drop in today's HTML, regenerate `index.html` + `archive.json`, commit with a deterministic message (e.g. `chore: archive YYYY-MM-DD`), and push. Use `git -c user.name=... -c user.email=...` rather than mutating global git config.

## Idempotency / re-run safety

The same day's run may be retried (manual `workflow_dispatch` after a transient failure). Pushes must be idempotent:

- If `YYYY-MM-DD.html` already exists in the archive repo, overwrite it.
- The `archive.json` entry for that date should be replaced, not appended-as-duplicate.
- `index.html` is regenerated from `archive.json` each time, so it's always consistent.

## Operational notes

- **Time zone:** All date stamps in HTML and `archive.json` are KST (`Asia/Seoul`). GitHub Actions runners are UTC — convert explicitly, don't rely on system locale.
- **Email body size:** `gov-support-archive` daily HTMLs reach ~1 MB once `ongoing_count` is in the 500–600 range. Gmail's SMTP send cap is 25 MB per message so size itself is fine, but Gmail's inbox renderer truncates bodies past ~102 KB ("[Message clipped]"). Don't try to fix this by trimming content — the truncation is client-side display only and the full message is still available. If it becomes a real problem, link out to the GitHub Pages copy instead of embedding everything.
- **Source instability:** Korean government sites change HTML structure without notice and frequently return 5xx. Each scraper should: (a) cache the previous successful payload, (b) on parse failure, fall back to yesterday's items rather than emitting an empty list (an empty list looks like "everything closed today" which is misleading), (c) surface the failure in the daily mail footer rather than aborting the run.

## Quick commands

```bash
# End-to-end local run, no mail, no push, no state mutation
python -m src.cli --dry-run

# Single archive (still writes mail + push unless --dry-run)
python -m src.cli --only kstartup-biz

# Day-1 / re-seed snapshots from current archive-repo HTML
python -m src.cli --seed

# Force re-send even if today's sent-marker already exists
python -m src.cli --force
```

## Things to verify before claiming "done"

1. Open the rendered `YYYY-MM-DD.html` in a browser — header color, emoji, footer bot name, and **top clip-warning banner** match the per-archive table above. (Archive-pushed copy: banner absent. Email copy: banner present.)
2. Click the banner's "브라우저에서 전체 보기 →" link — opens `https://pathcosmos.github.io/<archive>/YYYY-MM-DD.html` (the specific date file, not the index).
3. Send a test mail to yourself via Gmail SMTP and view it in Gmail web + Gmail mobile — table layout, deadline badges, top banner, and the bottom cross-archive footer block all render.
4. Click "전체 보기" in the email footer — lands on `pathcosmos.github.io/<archive>/` and auto-redirects to today's date.
5. Confirm `index.html`'s `<meta refresh>` URL is today's file, the date-count header (`📁 전체 아카이브 (N일)`) reflects the new total, and the date table includes today's entry as the top row with correct counts.
6. Confirm `archive.json` got a new entry with correct counts, no duplicate dates (re-runs replace, not append), and the top-level `title` field is preserved.
7. Confirm all five archive repos received a commit dated today **before** the corresponding email was sent (push-then-mail ordering).
8. The four cross-archive footer links each open the correct archive's GitHub Pages site (and the email being viewed does NOT link to itself).
