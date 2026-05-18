"""Dump placeholder daily HTMLs for the 5 archives to preview_*.html so they
can be opened in a browser for layout verification. Local-only helper; not
run by CI."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.cli import _placeholder_items  # type: ignore
from src.config.archives import ARCHIVE_ORDER, ARCHIVES
from src.render.daily_html import render_daily_html


def main() -> None:
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    out_dir = Path(__file__).resolve().parents[1]
    for key in ARCHIVE_ORDER:
        cfg = ARCHIVES[key]
        new, ongoing = _placeholder_items(cfg)
        html = render_daily_html(cfg, new, ongoing, today)
        path = out_dir / f"preview_{key}.html"
        path.write_text(html, encoding="utf-8")
        print(f"wrote {path}  ({len(html)} bytes)")


if __name__ == "__main__":
    main()
