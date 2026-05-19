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

    # gov-support 한정: AI컴퓨팅자원 지원포털(aiinfrahub.kr) 정적 링크. nipa 사업이
    # 실제로 여기서 운영되므로 GPU·AI 인프라 사용자에게 유용한 외부 참조.
    related_block = ""
    if current_key == "gov-support":
        related_block = (
            '<div style="margin-bottom:6px;color:#999;">🔗 관련 정보</div>'
            '<div style="line-height:1.8;margin-bottom:10px;">'
            '🖥️ <a href="https://aiinfrahub.kr/" '
            'style="color:#1a73e8;text-decoration:none;">국가 AI컴퓨팅자원 지원포털</a>'
            '</div>'
        )

    return (
        '<div style="text-align:center;padding:16px 8px;font-size:12px;'
        'color:#666;margin-top:12px;border-top:1px solid #eee;">'
        '<div style="margin-bottom:8px;">'
        '🗂️ 지난 아카이브 보기 → '
        f'<a href="{escape(cfg.pages_url)}" '
        'style="color:#1a73e8;text-decoration:none;font-weight:bold;">전체 보기</a>'
        '</div>'
        f'{related_block}'
        '<div style="margin-bottom:4px;color:#999;">📬 다른 메일링</div>'
        f'<div style="line-height:1.8;">{other_links}</div>'
        '</div>'
        f'<div style="text-align:center;padding:8px;font-size:12px;color:#999;">'
        f'{escape(cfg.footer_bot)}'
        '</div>'
    )
