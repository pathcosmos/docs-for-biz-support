"""state/ holds only the daily sent-marker. Item snapshots moved to Turso DB
(see src/db.py). The marker is a small JSON file committed back to this repo
each day so retries within the same day can skip already-sent archives."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = REPO_ROOT / "state"


def sent_marker_path(today: date) -> Path:
    return STATE_DIR / f"sent-{today.isoformat()}.marker"


def load_sent_marker(today: date) -> dict[str, str]:
    p = sent_marker_path(today)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def mark_sent(today: date, archive_key: str, message_id: str) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    p = sent_marker_path(today)
    cur = load_sent_marker(today)
    cur[archive_key] = message_id
    p.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
