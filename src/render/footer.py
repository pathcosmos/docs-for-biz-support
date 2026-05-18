"""The new footer block added for the rebuild: 'see archive' + cross-archive
navigation. The original mailing footer was just `<bot> | 자동발송`; this module
generates the block placed above it. The current archive is omitted from the
'다른 메일링' row to avoid a self-link."""

from __future__ import annotations

from html import escape

from ..config.archives import ARCHIVE_ORDER, ARCHIVES


def render_footer(current_key: str) -> str:
    cfg = ARCHIVES[current_key]
    others = [ARCHIVES[k] for k in ARCHIVE_ORDER if k != current_key]
    other_links = " · ".join(
        f'{a.nav_emoji} <a href="{escape(a.pages_url)}" '
        f'style="color:#1a73e8;text-decoration:none;">{escape(a.nav_label)}</a>'
        for a in others
    )

    return (
        '<div style="text-align:center;padding:16px 8px;font-size:12px;'
        'color:#666;margin-top:12px;border-top:1px solid #eee;">'
        '<div style="margin-bottom:8px;">'
        '🗂️ 지난 아카이브 보기 → '
        f'<a href="{escape(cfg.pages_url)}" '
        'style="color:#1a73e8;text-decoration:none;font-weight:bold;">전체 보기</a>'
        '</div>'
        '<div style="margin-bottom:4px;color:#999;">📬 다른 메일링</div>'
        f'<div style="line-height:1.8;">{other_links}</div>'
        '</div>'
        f'<div style="text-align:center;padding:8px;font-size:12px;color:#999;">'
        f'{escape(cfg.footer_bot)}'
        '</div>'
    )
