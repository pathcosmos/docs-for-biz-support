"""Adapter for the busan-startup archive.

**Current implementation: static JSON.** The source sites (busanstartup.kr,
pms.ripc.org, etc.) are JavaScript-rendered SPAs that don't expose an XHR
endpoint discoverable from the raw HTML. A real scraper would need browser
automation (Playwright/Selenium) which adds ~200 MB and ~30 s of CI install
overhead — not worth it for an archive of ~19 items that rarely change.

So this adapter emits items from a frozen JSON snapshot extracted from the
legacy 2026-05-13.html archive. The mail/push cycle runs daily and the
archive page stays alive, but the content is stable. When time permits, we
either:
  (a) hand-update _data/busan_startup_static.json with new items
  (b) implement a real scraper with browser automation in a future PR

Bookkeeping note: this adapter's items always classify as 'ongoing' for any
day they appear in (because they're identical from one run to the next),
which is the right semantic — they ARE ongoing facilities/services.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from . import register
from ..models import Item


_DATA_FILE = Path(__file__).resolve().parent / "_data" / "busan_startup_static.json"


@register("busan-startup")
def fetch() -> list[Item]:
    with _DATA_FILE.open(encoding="utf-8") as f:
        payload = json.load(f)
    return [_row_to_item(r) for r in payload["items"]]


def _row_to_item(r: dict) -> Item:
    deadline_str = r.get("deadline")
    deadline = date.fromisoformat(deadline_str) if deadline_str else None
    badges_str = r.get("badges_json")
    badges: tuple[str, ...] = ()
    if badges_str:
        try:
            badges = tuple(json.loads(badges_str))
        except (TypeError, ValueError):
            badges = ()
    return Item(
        stable_id=r["stable_id"],
        source_key=r["source_key"],
        title=r["title"],
        detail_url=r["detail_url"],
        category=r.get("category"),
        organizer=r.get("organizer"),
        amount=r.get("amount"),
        apply_period=r.get("apply_period"),
        deadline=deadline,
        region=r.get("region"),
        target=r.get("target"),
        summary=r.get("summary"),
        badges=badges,
    )
