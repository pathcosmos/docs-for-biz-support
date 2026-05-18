"""Push today's three files to each archive repo:

  - <repo>/YYYY-MM-DD.html  — daily mail body (for_email=False; no clip banner)
  - <repo>/archive.json     — counts manifest, today's entry replaced
  - <repo>/index.html       — regenerated landing page with meta-refresh to today

Strategy is `git clone --depth=1` over HTTPS with the PAT embedded in the
URL, write the 3 files, commit, push. Per-command `-c user.name=...` config
keeps the runner's global git untouched. Each repo is independent; one
failure is caught by the caller and doesn't cascade.

Run-time prerequisites:
- `git` binary on PATH (GitHub Actions runners have it).
- `ARCHIVE_PUSH_TOKEN` env var: fine-grained PAT with `contents:write` on the
  5 archive repos.
- `cfg.repo` (e.g. 'pathcosmos/gov-support-archive') is reachable.

Idempotency: same-day re-runs overwrite YYYY-MM-DD.html, replace today's
archive.json entry (no duplicate), regenerate index.html. If nothing changed
(byte-identical), git commit is skipped and we silently push nothing.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..config.archives import ArchiveConfig
from ..render.archive_json import update_archive_json
from ..render.index_html import render_index_html


logger = logging.getLogger(__name__)


BOT_NAME = "pathcosmos-bot"
BOT_EMAIL = "bot@users.noreply.github.com"


class PushError(RuntimeError):
    """Raised when a push to one archive repo fails. Caller catches per-archive."""


@dataclass
class PushResult:
    archive_key: str
    repo: str
    pushed: bool = False
    committed_sha: str | None = None
    note: str = ""    # 'no changes' / 'pushed' / 'dry-run' / error message
    files_changed: list[str] = field(default_factory=list)


def push_archive(
    cfg: ArchiveConfig,
    today: date,
    daily_html_for_archive: str,
    *,
    new_count: int,
    ongoing_count: int,
    expired_count: int,
    pat: str | None = None,
    dry_run: bool = False,
) -> PushResult:
    """Clone the archive repo, regenerate archive.json + index.html from the
    repo's CURRENT archive.json (preserving prior entries) plus today's counts,
    write the day HTML, commit if anything changed, push.

    `pat` defaults to env `ARCHIVE_PUSH_TOKEN`. `dry_run` does everything
    except `git push` (still clones + commits locally so you can inspect the
    would-be commit)."""

    pat = pat or os.environ.get("ARCHIVE_PUSH_TOKEN")
    if not pat and not dry_run:
        raise PushError("ARCHIVE_PUSH_TOKEN not set")

    result = PushResult(archive_key=cfg.key, repo=cfg.repo)
    today_iso = today.isoformat()

    with tempfile.TemporaryDirectory(prefix=f"push-{cfg.key}-") as tmp:
        tmp_path = Path(tmp)
        clone_url = (
            f"https://x-access-token:{pat}@github.com/{cfg.repo}.git"
            if pat else f"https://github.com/{cfg.repo}.git"
        )
        _run(["git", "clone", "--depth=1", "--quiet", clone_url, str(tmp_path)],
             cwd=None, redact=pat)

        # Read existing archive.json (None for first-ever push) so we preserve
        # prior entries while replacing today's. Same for index.html — we
        # always regenerate from the merged archive.json.
        existing_aj_path = tmp_path / "archive.json"
        existing_aj = existing_aj_path.read_text(encoding="utf-8") if existing_aj_path.exists() else None

        merged_json = update_archive_json(
            existing_content=existing_aj,
            title=cfg.title,
            today=today,
            new_count=new_count,
            ongoing_count=ongoing_count,
            expired_count=expired_count,
        )
        idx_html = render_index_html(cfg, merged_json)

        # Write the 3 files. encoding=utf-8 with no BOM matches the existing
        # 2026-04-15.html files in the archive repos.
        (tmp_path / f"{today_iso}.html").write_text(daily_html_for_archive, encoding="utf-8")
        existing_aj_path.write_text(merged_json, encoding="utf-8")
        (tmp_path / "index.html").write_text(idx_html, encoding="utf-8")

        # Detect what changed (porcelain output is empty when nothing changed).
        status = _run(
            ["git", "-C", str(tmp_path), "status", "--porcelain"],
            cwd=None, capture=True,
        )
        if not status.strip():
            result.note = "no changes"
            return result
        result.files_changed = [
            line[3:].strip() for line in status.splitlines() if line.strip()
        ]

        # Stage + commit + push. Per-command -c keeps global git config clean.
        identity = ["-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}"]
        _run(["git", "-C", str(tmp_path), *identity, "add", "."], cwd=None)
        _run(
            ["git", "-C", str(tmp_path), *identity, "commit", "-m",
             f"chore: archive {today_iso}"],
            cwd=None,
        )
        sha = _run(
            ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
            cwd=None, capture=True,
        ).strip()
        result.committed_sha = sha

        if dry_run:
            result.note = "dry-run (commit prepared, push skipped)"
            return result

        _run(["git", "-C", str(tmp_path), "push", "--quiet"],
             cwd=None, redact=pat)
        result.pushed = True
        result.note = "pushed"
    return result


def _run(
    cmd: list[str],
    *,
    cwd: str | None,
    capture: bool = False,
    redact: str | None = None,
) -> str:
    """Run a subprocess; raise PushError with a redacted error message on
    non-zero exit. `redact`, if set, is scrubbed from stderr so we don't leak
    the PAT in error logs."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, check=True,
            capture_output=True, text=True,
        )
        return proc.stdout
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "")
        stdout = (e.stdout or "")
        if redact:
            stderr = stderr.replace(redact, "***REDACTED***")
            stdout = stdout.replace(redact, "***REDACTED***")
        # Also redact in the command echoed back, in case the URL is in argv
        cmd_str = " ".join(cmd)
        if redact:
            cmd_str = cmd_str.replace(redact, "***REDACTED***")
        raise PushError(
            f"git command failed: {cmd_str}\n"
            f"stdout: {stdout.strip()}\n"
            f"stderr: {stderr.strip()}"
        ) from None
