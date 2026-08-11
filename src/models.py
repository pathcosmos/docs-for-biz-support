from __future__ import annotations

import re
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
    first_seen: date | None = None
    active_since: date | None = None


@dataclass(frozen=True)
class SourceReport:
    source_key: str
    status: str
    item_count: int
    error: str | None = None
    cached_snapshot_date: date | None = None

    @property
    def error_summary(self) -> str:
        """Short, recipient-safe cause; ``error`` retains full diagnostics."""
        error = " ".join((self.error or "원인 미상").split())
        if "Temporary failure in name resolution" in error or "ConnectError" in error:
            return "DNS 또는 네트워크 연결 실패"
        if "ConnectTimeout" in error:
            return "연결 시간 초과"
        if "ReadTimeout" in error:
            return "응답 시간 초과"
        if "API artifact unavailable after Ubuntu and macOS" in error:
            return "Ubuntu·macOS API 수집 실패"
        if "incomplete pagination" in error:
            match = re.search(r"\((\d+) items collected\)", error)
            count = f" ({match.group(1)}건 수집)" if match else ""
            return f"페이지네이션 불완전{count}"
        if "list page structure was not recognized" in error:
            return "목록 페이지 구조 변경 감지"
        return error if len(error) <= 180 else f"{error[:177]}..."

    @property
    def warning(self) -> str | None:
        if self.status == "fresh":
            return None
        if self.status == "static":
            return None
        if self.status == "fallback":
            cache_label = (
                f"{self.cached_snapshot_date.isoformat()} 스냅샷"
                if self.cached_snapshot_date else "최근 정상 캐시"
            )
            return (
                f"{self.source_key}: 수집 실패 — {cache_label} "
                f"{self.item_count}건 사용 ({self.error_summary})"
            )
        return (
            f"{self.source_key}: 수집 실패 및 사용 가능한 캐시 없음 "
            f"({self.error_summary})"
        )

    def as_dict(self) -> dict:
        return {
            "source_key": self.source_key,
            "status": self.status,
            "item_count": self.item_count,
            "error": self.error,
            "error_summary": self.error_summary,
            "cached_snapshot_date": (
                self.cached_snapshot_date.isoformat() if self.cached_snapshot_date else None
            ),
            "warning": self.warning,
        }


@dataclass(frozen=True)
class AdapterResult:
    items: list[Item]
    source_reports: tuple[SourceReport, ...] = ()

    @property
    def usable(self) -> bool:
        return all(r.status != "failed" for r in self.source_reports)

    @property
    def warnings(self) -> list[str]:
        return [warning for report in self.source_reports if (warning := report.warning)]


@dataclass
class ArchiveResult:
    archive_key: str
    items_new: list[Item] = field(default_factory=list)
    items_ongoing: list[Item] = field(default_factory=list)
    items_expired: list[Item] = field(default_factory=list)
    scraper_errors: list[str] = field(default_factory=list)
    html: str = ""
    mail_sent: bool = False
    pushed: bool = False
    pages_verified: bool = False
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
                f"expired={len(r.items_expired)} "
                f"mail={r.mail_sent} push={r.pushed} pages={r.pages_verified}"
                + (f" — {r.skipped_reason}" if r.skipped_reason else "")
            )
            for err in r.scraper_errors:
                lines.append(f"      ⚠ scraper: {err}")
        for err in self.fatal_errors:
            lines.append(f"  FATAL: {err}")
        return "\n".join(lines)
