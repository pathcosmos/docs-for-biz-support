"""archive.json maintainer. Each archive repo has one of these at its root
tracking every day's counts. Schema:

    {
      "entries": [
        {"date": "YYYY-MM-DD", "new_count": N, "ongoing_count": N, "expired_count": N},
        ...
      ],
      "title": "🏛️ 정부지원사업 모니터"
    }

`expired_count` is the field added with the DB / status-labeling rework. Old
entries from before the rework do NOT have it; we preserve them verbatim
(readers must `.get("expired_count", 0)`).
"""

from __future__ import annotations

import json
from datetime import date


def update_archive_json(
    *,
    existing_content: str | None,
    title: str,
    today: date,
    new_count: int,
    ongoing_count: int,
    expired_count: int,
) -> str:
    """Insert or replace today's entry; preserve the `title` and prior entries.

    `existing_content` is the current archive.json text from the archive repo
    (None / empty string on first run). Returned string is what we PUT back.

    Re-runs on the same day overwrite today's entry rather than appending
    duplicates — caller relies on this for workflow_dispatch retries.
    """
    today_iso = today.isoformat()

    if existing_content:
        data = json.loads(existing_content)
        entries = list(data.get("entries", []))
        # Use the existing title if present (don't clobber on accidental case
        # mismatches), but fall back to the passed title when missing.
        title_to_write = data.get("title") or title
    else:
        entries = []
        title_to_write = title

    # Drop any existing entry for today, then append the fresh one. Sort
    # ascending by date so the file is stable and the diff is minimal.
    entries = [e for e in entries if e.get("date") != today_iso]
    entries.append({
        "date": today_iso,
        "new_count": new_count,
        "ongoing_count": ongoing_count,
        "expired_count": expired_count,
    })
    entries.sort(key=lambda e: e.get("date", ""))

    return json.dumps(
        {"entries": entries, "title": title_to_write},
        ensure_ascii=False,
        indent=2,
    )


def latest_date(archive_json_content: str | None) -> str | None:
    """Return the ISO date of the most recent entry, or None if empty/missing.
    Used by `index_html.py` to decide the meta-refresh target."""
    if not archive_json_content:
        return None
    data = json.loads(archive_json_content)
    entries = data.get("entries") or []
    if not entries:
        return None
    return max((e.get("date") for e in entries if e.get("date")), default=None)
