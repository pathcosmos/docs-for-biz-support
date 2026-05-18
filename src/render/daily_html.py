"""Renders the daily HTML body for one archive. The output is both the email
body and the file pushed to <repo>/YYYY-MM-DD.html, byte-identical.

The inline-CSS layout matches the existing 2026-05-13.html outputs in the
five archive repos. Email clients (especially Gmail) don't reliably render
<style> tags, so EVERY style is inline. Do not refactor to external CSS."""

from __future__ import annotations

from datetime import date
from html import escape

from ..config.archives import ArchiveConfig
from ..config.categories import Category
from ..config.sources import SOURCES
from ..models import Item
from .footer import render_footer


def _deadline_badge(item: Item, today: date) -> str:
    """🔥 D-N red ≤5, ⚠️ D-N orange 6–14, no badge otherwise. Returns inline HTML
    or empty string. `today` is KST today."""
    if not item.deadline:
        return ""
    days = (item.deadline - today).days
    if days < 0:
        return ""  # already closed; don't shame the announcement
    if days <= 5:
        color = "#ea4335"
        emoji = "🔥"
    elif days <= 14:
        color = "#f57c00"
        emoji = "⚠️"
    else:
        return ""
    return (
        f'<span style="color:{color};font-weight:bold;">{emoji} D-{days}</span> '
    )


def _meta_table(item: Item, today: date) -> str:
    rows: list[str] = []

    def row(label: str, value_html: str) -> None:
        rows.append(
            f'<tr><td>{label}</td><td style="padding-left:12px;">{value_html}</td></tr>'
        )

    if item.organizer:
        row("🏢 주관기관", escape(item.organizer))
    if item.amount:
        row("💰 지원금액", escape(item.amount))
    if item.apply_period:
        badge = _deadline_badge(item, today)
        row("📅 신청기간", badge + escape(item.apply_period))
    if item.region:
        row("🗺️ 지역", escape(item.region))
    if item.target:
        row("👥 지원대상", escape(item.target))

    if not rows:
        return ""
    return (
        '<table style="font-size:13px;color:#444;line-height:1.8;">'
        + "".join(rows)
        + "</table>"
    )


def _item_card(item: Item, border_color: str, today: date) -> str:
    src = SOURCES.get(item.source_key)
    source_pill = ""
    if src:
        source_pill = (
            '<div style="margin-bottom:6px;">'
            '<span style="background:#e8eaed;color:#5f6368;'
            'padding:2px 8px;border-radius:4px;font-size:11px;margin-right:4px;">'
            f"{escape(src.display)}</span> </div>"
        )

    badges_html = "".join(
        # badges are pre-formatted strings like '🖥️ GPU/클라우드' — we wrap in pill
        f'<span style="background:#34a853;color:#fff;padding:2px 8px;'
        f'border-radius:4px;font-size:12px;margin-left:8px;">{escape(b)}</span>'
        for b in item.badges
    )

    summary_html = ""
    if item.summary:
        summary_html = (
            f'<p style="font-size:13px;color:#666;margin:8px 0 0;">'
            f"{escape(item.summary)}</p>"
        )

    return (
        f'<div style="background:#f8f9fa;border-left:4px solid {border_color};'
        'padding:14px 18px;margin:12px 0;border-radius:4px;">'
        '<div style="font-size:16px;font-weight:bold;margin-bottom:8px;">'
        '📌 '
        f'<a href="{escape(item.detail_url)}" '
        f'style="color:#1a73e8;text-decoration:none;">{escape(item.title)}</a>'
        f"{badges_html}"
        "</div>"
        f"{source_pill}"
        f"{_meta_table(item, today)}"
        f"{summary_html}"
        "</div>"
    )


def _category_section(category: Category, items: list[Item], today: date) -> str:
    if not items:
        return ""
    cards = "".join(_item_card(i, category.color, today) for i in items)
    return (
        '<div style="margin-top:24px;">'
        f'<div style="background:{category.color};color:#fff;padding:10px 16px;'
        'border-radius:6px;margin-bottom:4px;">'
        f'<h3 style="margin:0;font-size:16px;font-weight:bold;">'
        f"{category.emoji} {escape(category.name)} ({len(items)}건)</h3>"
        "</div>"
        f"{cards}"
        "</div>"
    )


def _new_section(items: list[Item], cfg: ArchiveConfig, today: date) -> str:
    if not items:
        return ""
    # Within 신규, items aren't category-grouped (matches existing output).
    # Border color stays #1a73e8 for new items regardless of archive theme,
    # which is what the gov-support output does.
    cards = "".join(_item_card(i, "#1a73e8", today) for i in items)
    return (
        '<div style="margin-top:28px;">'
        '<div style="background:#1a73e8;color:#fff;padding:12px 18px;border-radius:6px 6px 0 0;">'
        f'<h2 style="margin:0;font-size:18px;">🆕 신규 ({len(items)}건)</h2>'
        "</div>"
        '<div style="border:1px solid #e0e0e0;border-top:none;'
        'padding:12px 18px;border-radius:0 0 6px 6px;">'
        f"{cards}"
        "</div>"
        "</div>"
    )


def _expired_section(items: list[Item], today: date) -> str:
    """🔚 오늘 종료 (N건). Gray/muted styling, no D-N badges (already closed),
    no category grouping (whatever the items used to be, they're all closed now).
    Skipped entirely if items is empty."""
    if not items:
        return ""
    cards = "".join(_expired_item_card(i, today) for i in items)
    return (
        '<div style="margin-top:28px;">'
        '<div style="background:#9e9e9e;color:#fff;padding:12px 18px;border-radius:6px 6px 0 0;">'
        f'<h2 style="margin:0;font-size:18px;">🔚 오늘 종료 ({len(items)}건)</h2>'
        "</div>"
        '<div style="border:1px solid #e0e0e0;border-top:none;'
        'padding:12px 18px;border-radius:0 0 6px 6px;">'
        f"{cards}"
        "</div>"
        "</div>"
    )


def _expired_item_card(item: Item, today: date) -> str:
    """Item card variant for the expired section: same fields as the live cards
    but with a gray border, '🔚 신청기간 종료' marker prepended to the period
    row, and no D-N badge regardless of deadline date."""
    src = SOURCES.get(item.source_key)
    source_pill = ""
    if src:
        source_pill = (
            '<div style="margin-bottom:6px;">'
            '<span style="background:#e8eaed;color:#5f6368;'
            'padding:2px 8px;border-radius:4px;font-size:11px;margin-right:4px;">'
            f"{escape(src.display)}</span> </div>"
        )

    # Build meta table WITHOUT the D-N badge — already closed.
    rows: list[str] = []

    def row(label: str, value_html: str) -> None:
        rows.append(
            f'<tr><td>{label}</td><td style="padding-left:12px;">{value_html}</td></tr>'
        )

    if item.organizer:
        row("🏢 주관기관", escape(item.organizer))
    if item.amount:
        row("💰 지원금액", escape(item.amount))
    if item.apply_period:
        row("📅 신청기간",
            f'<span style="color:#9e9e9e;">🔚 종료</span> {escape(item.apply_period)}')
    if item.region:
        row("🗺️ 지역", escape(item.region))
    if item.target:
        row("👥 지원대상", escape(item.target))
    meta_table = (
        '<table style="font-size:13px;color:#888;line-height:1.8;">'
        + "".join(rows) + "</table>"
        if rows else ""
    )

    summary_html = ""
    if item.summary:
        summary_html = (
            f'<p style="font-size:13px;color:#999;margin:8px 0 0;">'
            f"{escape(item.summary)}</p>"
        )

    return (
        '<div style="background:#fafafa;border-left:4px solid #9e9e9e;'
        'padding:14px 18px;margin:12px 0;border-radius:4px;opacity:0.85;">'
        '<div style="font-size:16px;font-weight:bold;margin-bottom:8px;color:#666;">'
        '📌 '
        f'<a href="{escape(item.detail_url)}" '
        f'style="color:#666;text-decoration:line-through;">{escape(item.title)}</a>'
        "</div>"
        f"{source_pill}"
        f"{meta_table}"
        f"{summary_html}"
        "</div>"
    )


def _ongoing_section(items: list[Item], cfg: ArchiveConfig, today: date) -> str:
    if not items:
        return ""
    if cfg.categories:
        # Bucket items into known categories in declared order; items whose
        # category doesn't match any bucket get rendered last with the neutral
        # gray border.
        buckets: dict[str, list[Item]] = {c.key: [] for c in cfg.categories}
        leftovers: list[Item] = []
        for i in items:
            if i.category and i.category in buckets:
                buckets[i.category].append(i)
            else:
                leftovers.append(i)
        body = "".join(_category_section(c, buckets[c.key], today) for c in cfg.categories)
        if leftovers:
            body += "".join(_item_card(i, "#5f6368", today) for i in leftovers)
    else:
        body = "".join(_item_card(i, "#5f6368", today) for i in items)

    return (
        '<div style="margin-top:28px;">'
        '<div style="background:#5f6368;color:#fff;padding:12px 18px;border-radius:6px 6px 0 0;">'
        f'<h2 style="margin:0;font-size:18px;">📋 진행 중 ({len(items)}건)</h2>'
        "</div>"
        '<div style="border:1px solid #e0e0e0;border-top:none;'
        'padding:12px 18px;border-radius:0 0 6px 6px;">'
        f"{body}"
        "</div>"
        "</div>"
    )


def _clip_banner(cfg: ArchiveConfig, today: date) -> str:
    """Top-of-mail banner that links to the canonical archive copy. Gmail
    truncates message bodies past ~102KB ([Message clipped]); this gives
    recipients a one-click escape. The target URL points at the specific
    date so the link stays correct even after future days are archived."""
    full_url = f"{cfg.pages_url}{today.isoformat()}.html"
    return (
        '<div style="background:#fff8e1;border:1px solid #f0c244;'
        'border-radius:6px;padding:10px 14px;margin:0 0 14px;font-size:13px;'
        'color:#7a5a00;text-align:center;">'
        '📄 메일이 길어 잘렸나요? '
        f'<a href="{escape(full_url)}" '
        'style="color:#1a73e8;text-decoration:none;font-weight:bold;">'
        '브라우저에서 전체 보기 →</a>'
        "</div>"
    )


def render_daily_html(
    cfg: ArchiveConfig,
    items_new: list[Item],
    items_ongoing: list[Item],
    today: date,
    items_expired: list[Item] | None = None,
    scraper_errors: list[str] | None = None,
    for_email: bool = True,
) -> str:
    """Render the daily HTML. `for_email=True` (default) includes the top
    'view in browser' clip-warning banner. The version pushed to the archive
    repo passes `for_email=False` since the reader is already viewing the
    canonical copy. `items_expired` defaults to []; only rendered as the
    `🔚 오늘 종료` section when non-empty."""
    items_expired = items_expired or []
    new_count = len(items_new)
    ongoing_count = len(items_ongoing)
    expired_count = len(items_expired)

    scraper_warnings = ""
    if scraper_errors:
        warnings = "".join(
            f'<div>⚠️ {escape(e)}</div>' for e in scraper_errors
        )
        scraper_warnings = (
            '<div style="background:#fff3cd;border-left:4px solid #f57c00;'
            'padding:10px 14px;margin:12px 0;color:#7a4a00;font-size:13px;'
            'border-radius:4px;">'
            "<strong>일부 소스 스크래핑 실패 — 어제 데이터 사용</strong>"
            f"{warnings}"
            "</div>"
        )

    return (
        "<!DOCTYPE html>"
        '<html><head><meta charset="utf-8"></head>'
        '<body style="font-family:\'Apple SD Gothic Neo\',\'맑은 고딕\',sans-serif;'
        'max-width:680px;margin:0 auto;padding:20px;color:#333;">'
        + (_clip_banner(cfg, today) if for_email else "")
        + f'<div style="background:{cfg.header_color};color:#fff;padding:20px 24px;'
        'border-radius:8px 8px 0 0;">'
        f'<h1 style="margin:0;font-size:22px;">{escape(cfg.title)}</h1>'
        f'<p style="margin:6px 0 0;font-size:14px;opacity:0.9;">'
        + f"{today.isoformat()} | 신규 {new_count}건 · 진행중 {ongoing_count}건"
        + (f" · 종료 {expired_count}건" if expired_count else "")
        + "</p></div>"
        '<div style="border:1px solid #e0e0e0;border-top:none;'
        'padding:20px 24px;border-radius:0 0 8px 8px;">'
        f"{scraper_warnings}"
        f"{_new_section(items_new, cfg, today)}"
        f"{_ongoing_section(items_ongoing, cfg, today)}"
        f"{_expired_section(items_expired, today)}"
        "</div>"
        f"{render_footer(cfg.key)}"
        "</body></html>"
    )
