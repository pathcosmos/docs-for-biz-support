from __future__ import annotations

from dataclasses import dataclass

from .categories import (
    CATEGORIES_BUSAN_STARTUP,
    CATEGORIES_KSTARTUP_GLOBAL,
    CATEGORIES_KSTARTUP_MENTORING,
    Category,
)


@dataclass(frozen=True)
class ArchiveConfig:
    """Per-archive constants. Adding a sixth archive = one new entry here +
    one adapter module + (usually) one new scraper."""

    key: str                    # short id used in CLI --only, state filenames, log lines
    repo: str                   # 'pathcosmos/<repo-name>'
    title: str                  # full h1 with emoji, e.g. '🏛️ 정부지원사업 모니터'
    nav_emoji: str              # emoji only, for the cross-archive footer row
    nav_label: str              # short Korean label for the footer row
    header_color: str           # daily-HTML header bar background
    footer_bot: str             # last footer line, e.g. 'Gov Support Bot | 자동발송'
    subject_prefix: str         # subject bracket, e.g. '[정부지원]'
    pages_url: str              # https://pathcosmos.github.io/<repo>/
    cc_env: str | None          # name of GH Actions secret holding Cc list; None if no Cc
    sources: tuple[str, ...]    # source_keys this archive's adapter consumes
    categories: tuple[Category, ...] | None  # None = ungrouped (flat 진행중 list)
    curation_enabled: bool = False  # PR-CURATION: render 10-section curated layout instead of the
                                    # legacy 🆕/📋/🔚 trio. Only enabled for gov-support (largest
                                    # archive); other 4 keep their original format until user OKs.


ARCHIVES: dict[str, ArchiveConfig] = {
    "gov-support": ArchiveConfig(
        key="gov-support",
        repo="pathcosmos/gov-support-archive",
        title="🏛️ 정부지원사업 모니터",
        nav_emoji="🏛️",
        nav_label="정부지원사업",
        header_color="#1a73e8",
        footer_bot="Gov Support Bot | 자동발송",
        subject_prefix="[정부지원]",
        pages_url="https://pathcosmos.github.io/gov-support-archive/",
        cc_env="MAIL_CC_GOV_SUPPORT",
        sources=(
            "bizinfo", "smes", "ntis", "iris", "smart_factory",
            "cbtp", "djtp", "gbtp", "jntp", "utp", "btp",
            "nipa",
        ),
        categories=None,
        curation_enabled=True,
    ),
    "busan-startup": ArchiveConfig(
        key="busan-startup",
        repo="pathcosmos/busan-startup-archive",
        title="🚀 부산창업 서비스",
        nav_emoji="🚀",
        nav_label="부산창업",
        header_color="#0d47a1",
        footer_bot="Busan Startup Bot | 자동발송",
        subject_prefix="[부산창업]",
        pages_url="https://pathcosmos.github.io/busan-startup-archive/",
        cc_env=None,
        sources=("busan_startup", "busan_service"),
        categories=CATEGORIES_BUSAN_STARTUP,
    ),
    "kstartup-biz": ArchiveConfig(
        key="kstartup-biz",
        repo="pathcosmos/kstartup-biz-archive",
        title="💼 K-Startup 사업화",
        nav_emoji="💼",
        nav_label="K-Startup 사업화",
        header_color="#1a73e8",
        footer_bot="K-Startup Bot | 자동발송",
        subject_prefix="[K-Startup 사업화]",
        pages_url="https://pathcosmos.github.io/kstartup-biz-archive/",
        cc_env=None,
        sources=("kstartup_biz",),
        categories=None,
    ),
    "kstartup-mentoring": ArchiveConfig(
        key="kstartup-mentoring",
        repo="pathcosmos/kstartup-mentoring-archive",
        title="🧭 K-Startup 멘토링 · R&D",
        nav_emoji="🧭",
        nav_label="멘토링·R&D",
        header_color="#00897b",
        footer_bot="K-Startup Bot | 자동발송",
        subject_prefix="[K-Startup 멘토링]",
        pages_url="https://pathcosmos.github.io/kstartup-mentoring-archive/",
        cc_env=None,
        sources=("kstartup_mentoring",),
        categories=CATEGORIES_KSTARTUP_MENTORING,
    ),
    "kstartup-global": ArchiveConfig(
        key="kstartup-global",
        repo="pathcosmos/kstartup-global-archive",
        title="🌏 K-Startup 글로벌 · 시설",
        nav_emoji="🌏",
        nav_label="글로벌·시설",
        header_color="#0277bd",
        footer_bot="K-Startup Bot | 자동발송",
        subject_prefix="[K-Startup 글로벌]",
        pages_url="https://pathcosmos.github.io/kstartup-global-archive/",
        cc_env=None,
        sources=("kstartup_global",),
        categories=CATEGORIES_KSTARTUP_GLOBAL,
    ),
}


ARCHIVE_ORDER: tuple[str, ...] = (
    "gov-support", "busan-startup",
    "kstartup-biz", "kstartup-mentoring", "kstartup-global",
)
