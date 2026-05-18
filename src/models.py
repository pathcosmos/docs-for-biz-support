from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Item:
    """A single program/announcement after scrape + adapt. The unit that flows
    scrapers → diff → renderer → mail. `stable_id` is the diff key; `source_key`
    drives the source-tag pill via SOURCE_TAGS; `category` only matters for the
    three archives that group their 진행중 section."""

    stable_id: str
    source_key: str
    title: str
    detail_url: str
    category: str | None = None
    organizer: str | None = None
    amount: str | None = None
    apply_period: str | None = None
    deadline: date | None = None
    region: str | None = None
    target: str | None = None
    summary: str | None = None
    badges: tuple[str, ...] = ()


@dataclass
class ArchiveResult:
    archive_key: str
    items_new: list[Item] = field(default_factory=list)
    items_ongoing: list[Item] = field(default_factory=list)
    scraper_errors: list[str] = field(default_factory=list)
    html: str = ""
    mail_sent: bool = False
    pushed: bool = False
    skipped_reason: str | None = None


@dataclass
class RunReport:
    date: date
    results: list[ArchiveResult] = field(default_factory=list)
    fatal_errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"== Run {self.date.isoformat()} =="]
        for r in self.results:
            tag = "OK " if r.mail_sent and r.pushed else "PARTIAL"
            if r.skipped_reason:
                tag = "SKIP"
            lines.append(
                f"  [{tag}] {r.archive_key}: "
                f"new={len(r.items_new)} ongoing={len(r.items_ongoing)} "
                f"mail={r.mail_sent} push={r.pushed}"
                + (f" — {r.skipped_reason}" if r.skipped_reason else "")
            )
            for err in r.scraper_errors:
                lines.append(f"      ⚠ scraper: {err}")
        for err in self.fatal_errors:
            lines.append(f"  FATAL: {err}")
        return "\n".join(lines)
