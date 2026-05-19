"""Mail-body curation — 10-section priority-preemption classifier.

Goal: organize gov-support's ~1400 daily items so the recipient can scan
the mail top-to-bottom without missing a time-critical announcement.

Each item appears in EXACTLY ONE section: the highest-priority section it
matches. Priority order (safety-net first):

  1. 🔥 마감 임박 D-5
  2. ⚠️ 마감 D-6 ~ D-14
  3. 🆕🖥️ 오늘 신규 — GPU·AI 인프라
  4. 🏛️ 부산 사업
  5. 🖥️ GPU·AI 인프라 (진행중)
  6. 🆕 오늘 신규 — 그 외
  7. 🔁 재공고·연장
  8. 📋 진행 중 — 그 외 (deadline 있음)
  9. ⛊ 상시 접수 (deadline=None)
 10. 🔚 오늘 종료

Within each section: deadline ASC, None last.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from ..models import Item
from .labels import GPU_AI_BADGE, is_busan, is_reannouncement


# 섹션 식별자 — 렌더러가 헤더 색/emoji 매핑할 때 사용.
SEC_DEADLINE_5    = "deadline_5"
SEC_DEADLINE_14   = "deadline_14"
SEC_NEW_GPU       = "new_gpu"
SEC_BUSAN         = "busan"
SEC_GPU_ONGOING   = "gpu_ongoing"
SEC_NEW_OTHER     = "new_other"
SEC_REANNOUNCE    = "reannounce"
SEC_ONGOING       = "ongoing"
SEC_ALWAYS_OPEN   = "always_open"
SEC_EXPIRED       = "expired"


# 섹션 메타 — 헤더 출력에 사용. 색은 plan 표와 일치.
SECTION_META: dict[str, tuple[str, str, str]] = {
    # key             -> (emoji+title,                    header_color,  card_border_color)
    SEC_DEADLINE_5:    ("🔥 마감 임박 (D-5)",              "#ea4335",    "#ea4335"),
    SEC_DEADLINE_14:   ("⚠️ 마감 임박 (D-6 ~ D-14)",       "#f57c00",    "#f57c00"),
    SEC_NEW_GPU:       ("🆕🖥️ 오늘 신규 — GPU·AI 인프라",  "#7b1fa2",    "#7b1fa2"),
    SEC_BUSAN:         ("🏛️ 부산 사업",                    "#0d47a1",    "#0d47a1"),
    SEC_GPU_ONGOING:   ("🖥️ GPU·AI 인프라 (진행중)",       "#9c27b0",    "#9c27b0"),
    SEC_NEW_OTHER:     ("🆕 오늘 신규 — 그 외",             "#1a73e8",    "#1a73e8"),
    SEC_REANNOUNCE:    ("🔁 재공고·연장",                   "#f9a825",    "#f9a825"),
    SEC_ONGOING:       ("📋 진행 중 — 그 외",               "#5f6368",    "#5f6368"),
    SEC_ALWAYS_OPEN:   ("⛊ 상시 접수",                      "#00897b",    "#00897b"),
    SEC_EXPIRED:       ("🔚 오늘 종료",                     "#9e9e9e",    "#9e9e9e"),
}


@dataclass
class CurationSection:
    key: str
    title: str         # emoji + label, 예: '🔥 마감 임박 (D-5)'
    header_color: str  # CSS hex
    border_color: str  # item-card border-left color
    items: list[Item] = field(default_factory=list)


_FAR_FUTURE = date(9999, 12, 31)


def _sort_key(i: Item) -> date:
    """deadline ASC, None을 가장 뒤로."""
    return i.deadline or _FAR_FUTURE


def classify_for_curation(
    *,
    items_new: list[Item],
    items_ongoing: list[Item],
    items_expired: list[Item],
    today: date,
) -> list[CurationSection]:
    """Return the 10 sections in display order. Empty sections still appear
    in the result list — the renderer drops them by checking `items`."""
    d5_cutoff = today + timedelta(days=5)
    d14_cutoff = today + timedelta(days=14)

    # 한 항목이 두 섹션에 들어가지 않도록 'stable_id 선점' 사용.
    claimed: set[str] = set()
    sections: dict[str, list[Item]] = {k: [] for k in SECTION_META}

    def _put(key: str, item: Item) -> None:
        claimed.add(item.stable_id)
        sections[key].append(item)

    # Pool: new + ongoing (expired는 마지막 섹션에 전용).
    pool = items_new + items_ongoing
    new_ids = {i.stable_id for i in items_new}

    # 1. D-5 (긴급)
    for i in pool:
        if i.stable_id in claimed: continue
        if i.deadline and today <= i.deadline <= d5_cutoff:
            _put(SEC_DEADLINE_5, i)

    # 2. D-6 ~ D-14
    for i in pool:
        if i.stable_id in claimed: continue
        if i.deadline and (today + timedelta(days=6)) <= i.deadline <= d14_cutoff:
            _put(SEC_DEADLINE_14, i)

    # 3. 신규 + GPU
    for i in pool:
        if i.stable_id in claimed: continue
        if i.stable_id in new_ids and GPU_AI_BADGE in i.badges:
            _put(SEC_NEW_GPU, i)

    # 4. 부산 (title + organizer 매칭)
    for i in pool:
        if i.stable_id in claimed: continue
        if is_busan(i.title, i.organizer):
            _put(SEC_BUSAN, i)

    # 5. GPU 진행중 (위에서 안 잡힌 것)
    for i in pool:
        if i.stable_id in claimed: continue
        if GPU_AI_BADGE in i.badges:
            _put(SEC_GPU_ONGOING, i)

    # 6. 신규 그 외
    for i in pool:
        if i.stable_id in claimed: continue
        if i.stable_id in new_ids:
            _put(SEC_NEW_OTHER, i)

    # 7. 재공고/연장
    for i in pool:
        if i.stable_id in claimed: continue
        if is_reannouncement(i.title):
            _put(SEC_REANNOUNCE, i)

    # 8. 진행중 그 외 (deadline 있음)
    for i in pool:
        if i.stable_id in claimed: continue
        if i.deadline is not None:
            _put(SEC_ONGOING, i)

    # 9. 상시 접수 (deadline=None)
    for i in pool:
        if i.stable_id in claimed: continue
        _put(SEC_ALWAYS_OPEN, i)

    # 10. expired — 전용 pool
    sections[SEC_EXPIRED] = list(items_expired)

    # 각 섹션 내 정렬 (expired 제외 — 모두 deadline ASC).
    for key in sections:
        sections[key].sort(key=_sort_key)

    # 출력 순서대로 CurationSection 객체 빌드.
    order = [
        SEC_DEADLINE_5, SEC_DEADLINE_14,
        SEC_NEW_GPU, SEC_BUSAN, SEC_GPU_ONGOING, SEC_NEW_OTHER,
        SEC_REANNOUNCE, SEC_ONGOING, SEC_ALWAYS_OPEN,
        SEC_EXPIRED,
    ]
    result: list[CurationSection] = []
    for key in order:
        title, hdr, brd = SECTION_META[key]
        result.append(CurationSection(
            key=key, title=title, header_color=hdr, border_color=brd,
            items=sections[key],
        ))
    return result
