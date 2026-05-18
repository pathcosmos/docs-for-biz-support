"""Archive landing page (`index.html`) generator. Regenerated from archive.json
on every run so the date table and the meta-refresh target always match the
latest entry.

Notable contracts (mirrored from CLAUDE.md):
- `<title>` = `{archive.title} — Archive`
- Hero box background is uniformly `#1a73e8` across all 5 archives — NOT the
  per-archive daily-HTML header color. This is intentional consistency.
- 3-second `<meta http-equiv="refresh">` to the latest day file.
- Date table is newest-first. Each row's right-side label includes a `· 종료 N건`
  suffix only when `expired_count > 0` (preserves the original look for days
  before the DB rework).
- This is browser-only output — `<style>` blocks are fine here (unlike the
  daily HTML which is also an email body).
"""

from __future__ import annotations

import json
from html import escape
from typing import Iterable

from ..config.archives import ArchiveConfig


def render_index_html(cfg: ArchiveConfig, archive_json_content: str) -> str:
    """Render the archive's index.html. `archive_json_content` is the latest
    archive.json text (already updated for today)."""
    data = json.loads(archive_json_content)
    entries: list[dict] = data.get("entries") or []
    title: str = data.get("title") or cfg.title

    # Newest first for display + meta-refresh target.
    entries_desc = sorted(entries, key=lambda e: e.get("date", ""), reverse=True)
    latest = entries_desc[0]["date"] if entries_desc else None

    refresh_meta = (
        f'<meta http-equiv="refresh" content="3; url=./{latest}.html">'
        if latest else ""
    )
    hero_redirect = (
        f"3초 뒤 최신 아카이브({escape(latest)})로 이동합니다. "
        f'<a href="./{escape(latest)}.html" style="color:#fff;text-decoration:underline;">'
        f"바로 열기</a>"
        if latest else
        "아직 아카이브된 항목이 없습니다."
    )

    rows = "".join(_row(e) for e in entries_desc)
    repo_url = f"https://github.com/{cfg.repo}"

    return (
        "<!DOCTYPE html>\n"
        '<html lang="ko"><head>\n'
        '<meta charset="utf-8">\n'
        f"<title>{escape(title)} — Archive</title>\n"
        f"{refresh_meta}\n"
        "<style>\n"
        "body{font-family:-apple-system,'Apple SD Gothic Neo','맑은 고딕',sans-serif;"
        "max-width:720px;margin:0 auto;padding:24px;color:#333;}\n"
        ".hero{background:#1a73e8;color:#fff;padding:20px 24px;border-radius:8px;}\n"
        ".hero h1{margin:0;font-size:20px;}\n"
        ".hero p{margin:6px 0 0;font-size:13px;opacity:.9;}\n"
        "table{width:100%;border-collapse:collapse;margin-top:16px;}\n"
        "tr{border-bottom:1px solid #eee;}\n"
        "tr:hover{background:#f8f9fa;}\n"
        "</style>\n"
        "</head><body>\n"
        '<div class="hero">\n'
        f"  <h1>{escape(title)}</h1>\n"
        f"  <p>{hero_redirect}</p>\n"
        "</div>\n"
        f'<h2 style="margin-top:24px;font-size:16px;color:#666;">'
        f"📁 전체 아카이브 ({len(entries_desc)}일)</h2>\n"
        f"<table>{rows}</table>\n"
        '<div style="margin-top:24px;text-align:center;font-size:12px;color:#bbb;">\n'
        f'  <a href="{escape(repo_url)}" style="color:#bbb;">GitHub Repo</a>\n'
        "</div>\n"
        "</body></html>\n"
    )


def _row(entry: dict) -> str:
    d = entry.get("date", "")
    new_n = int(entry.get("new_count", 0))
    ongoing_n = int(entry.get("ongoing_count", 0))
    expired_n = int(entry.get("expired_count", 0))
    label_parts = [f"신규 {new_n}건", f"진행중 {ongoing_n}건"]
    if expired_n:
        label_parts.append(f"종료 {expired_n}건")
    label = " · ".join(label_parts)
    return (
        "<tr>\n"
        f'  <td style="padding:8px 12px;">'
        f'<a href="./{escape(d)}.html" style="color:#1a73e8;text-decoration:none;">'
        f"{escape(d)}</a></td>\n"
        f'  <td style="padding:8px 12px;text-align:right;color:#666;">'
        f"{escape(label)}</td>\n"
        "</tr>\n"
    )
