"""Mail-body curation — 12-section priority-preemption classifier.

Goal: organize gov-support's ~1400 daily items so the recipient can scan
the mail top-to-bottom without missing a time-critical announcement, and so
that programs relevant to a 부산/경남/경북-based, 제조·AI 연관 중견·중소기업
surface ahead of merely-urgent-but-irrelevant ones (PR-PRIORITY).

Each item appears in EXACTLY ONE section: the highest-priority section it
matches. Priority order (safety-net first):

  1. 🔥🎯 마감임박(0~14일) + 우선조건 동시충족
  2. 🎯 우선조건 충족 — 부산·경남·경북 · 제조·AI · 중견·중소
  3. 🔥 마감 임박 (D-5) — 우선조건 미충족
  4. ⚠️ 마감 임박 (D-6 ~ D-14) — 우선조건 미충족
  5. 🆕🖥️ 오늘 신규 — GPU·AI 인프라
  6. 🗺️ 지역 우선 — 부산·경남·경북 (우선조건 threshold 미달, 지역만 매칭)
  7. 🖥️ GPU·AI 인프라 (진행중)
  8. 🆕 오늘 신규 — 그 외
  9. 🔁 재공고·연장
 10. 📋 진행 중 — 그 외 (deadline 있음)
 11. ⛊ 상시 접수 (deadline=None)
 12. 🔚 오늘 종료

Within each section: deadline ASC, None last.

"우선조건"(`is_priority_match`)은 지역(부산/경남/경북) + 산업(제조/AI) +
기업규모(중견/중소) 3개 신호를 가중치 점수로 합산해 `PRIORITY_SCORE_THRESHOLD`
이상이면 성립 — AND도 OR도 아닌 스코어링 방식 (자세한 근거는
/Users/lanco/.claude/plans/wondrous-sprouting-reef.md 참고). 순수 지역매칭만
으로는 threshold를 못 넘기 때문에(4점 < 6점), 섹션 6이 그 항목들의 유일한
전용 자리로 남는다 — 부산 전용이던 옛 섹션을 지우지 않고 3개 지역으로 넓힌
이유. GPU 신호는 우선조건 스코어에 넣지 않는다 — GPU 항목은 전용 섹션
(🆕🖥️/🖥️, badge 기반)이 따로 잡으므로 여기서 가산하면 이중 우대가 된다.
산업(0~3) + 규모(0~2) 최대 5점 < threshold 6이라 지역 신호 없이는 우선조건이
성립하지 않는 것도 의도된 동작.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from ..models import Item
from .labels import GPU_AI_BADGE, is_reannouncement, matched_regions, regional_score


# 섹션 식별자 — 렌더러가 헤더 색/emoji 매핑할 때 사용.
SEC_URGENT_PRIORITY = "urgent_priority"
SEC_PRIORITY_MATCH  = "priority_match"
SEC_DEADLINE_5    = "deadline_5"
SEC_DEADLINE_14   = "deadline_14"
SEC_NEW_GPU       = "new_gpu"
SEC_REGIONAL      = "regional"
SEC_GPU_ONGOING   = "gpu_ongoing"
SEC_NEW_OTHER     = "new_other"
SEC_REANNOUNCE    = "reannounce"
SEC_ONGOING       = "ongoing"
SEC_ALWAYS_OPEN   = "always_open"
SEC_EXPIRED       = "expired"

# 항목 수가 너무 커질 수 있는 섹션만 캡을 둔다 (safety net) — 없는 키는 무제한.
SECTION_MAX_ITEMS: dict[str, int] = {
    SEC_PRIORITY_MATCH: 50,
    SEC_REGIONAL: 50,
}


# 섹션 메타 — 헤더 출력에 사용. 색은 plan 표와 일치.
SECTION_META: dict[str, tuple[str, str, str]] = {
    # key                 -> (emoji+title,                                        header_color,  card_border_color)
    SEC_URGENT_PRIORITY:  ("🔥🎯 마감임박 + 우선조건 동시충족",                    "#b71c1c",    "#b71c1c"),
    SEC_PRIORITY_MATCH:   ("🎯 우선조건 충족 — 부산·경남·경북 · 제조·AI · 중견·중소", "#283593", "#283593"),
    SEC_DEADLINE_5:       ("🔥 마감 임박 (D-5)",                                   "#ea4335",    "#ea4335"),
    SEC_DEADLINE_14:      ("⚠️ 마감 임박 (D-6 ~ D-14)",                           "#f57c00",    "#f57c00"),
    SEC_NEW_GPU:          ("🆕🖥️ 오늘 신규 — GPU·AI 인프라",                      "#7b1fa2",    "#7b1fa2"),
    SEC_REGIONAL:         ("🗺️ 지역 우선 — 부산·경남·경북",                        "#0d47a1",    "#0d47a1"),
    SEC_GPU_ONGOING:      ("🖥️ GPU·AI 인프라 (진행중)",                           "#9c27b0",    "#9c27b0"),
    SEC_NEW_OTHER:        ("🆕 오늘 신규 — 그 외",                                 "#1a73e8",    "#1a73e8"),
    SEC_REANNOUNCE:       ("🔁 재공고·연장",                                       "#f9a825",    "#f9a825"),
    SEC_ONGOING:          ("📋 진행 중 — 그 외",                                   "#5f6368",    "#5f6368"),
    SEC_ALWAYS_OPEN:      ("⛊ 상시 접수",                                          "#00897b",    "#00897b"),
    SEC_EXPIRED:          ("🔚 오늘 종료",                                         "#9e9e9e",    "#9e9e9e"),
}


@dataclass
class CurationSection:
    key: str
    title: str         # emoji + label, 예: '🔥 마감 임박 (D-5)'
    header_color: str  # CSS hex
    border_color: str  # item-card border-left color
    items: list[Item] = field(default_factory=list)
    overflow_count: int = 0


_FAR_FUTURE = date(9999, 12, 31)


def _sort_key(i: Item) -> date:
    """deadline ASC, None을 가장 뒤로."""
    return i.deadline or _FAR_FUTURE


def _haystack(item: Item) -> str:
    """title+summary+organizer+target을 합친 소문자 검색 텍스트. industry_score
    /size_score/is_midsme_ai_mfg_gpu_item이 공유하는 헬퍼 — 토큰 리스트가
    갈라지지 않도록 한 곳에서만 조립한다."""
    return " ".join(
        x for x in [item.title, item.summary, item.organizer, item.target] if x
    ).lower()


_MIDSME_HARD_TOKENS = ("중견", "중소")
_MIDSME_AI_TOKENS = ("ai", "인공지능", "llm", "머신러닝", "딥러닝")
_MIDSME_GPU_TOKENS = ("gpu", "npu", "가속기", "고성능컴퓨팅", "hpc")
_MIDSME_MFG_TOKENS = ("제조", "스마트공장", "공정", "설비", "생산")


def is_midsme_ai_mfg_gpu_item(item: Item, threshold: int = 3) -> bool:
    """PR-PRIORITY의 `is_priority_match`로 대체됨(섹션 라우팅에서는 더 이상
    호출되지 않음) — 참고/하위호환용으로 유지."""
    haystack = _haystack(item)
    if not any(token in haystack for token in _MIDSME_HARD_TOKENS):
        return False

    score = 0
    if any(token in haystack for token in _MIDSME_AI_TOKENS):
        score += 2
    if any(token in haystack for token in _MIDSME_GPU_TOKENS):
        score += 2
    if any(token in haystack for token in _MIDSME_MFG_TOKENS):
        score += 1
    return score >= threshold


def industry_score(item: Item) -> int:
    """제조/AI 연관도. AI +2, 제조 +1 (중복 가산, 최대 3).

    GPU 토큰은 가산하지 않는다 — GPU 항목은 전용 섹션(SEC_NEW_GPU/
    SEC_GPU_ONGOING)이 badge로 따로 잡으므로 우선조건에서까지 우대하지
    않는다."""
    haystack = _haystack(item)
    score = 0
    if any(t in haystack for t in _MIDSME_AI_TOKENS):
        score += 2
    if any(t in haystack for t in _MIDSME_MFG_TOKENS):
        score += 1
    return score


def size_score(item: Item) -> int:
    """기업규모 적합도. '중견' 언급 +2, ('중견' 없이) '중소'만 언급 +1,
    둘 다 없으면 0 (중견/중소 동시 가산하지 않음 — title에 흔히 함께 등장하는
    관용구라 이중가산하면 신호가 과대평가됨)."""
    haystack = _haystack(item)
    if "중견" in haystack:
        return 2
    if "중소" in haystack:
        return 1
    return 0


def priority_score(item: Item) -> int:
    """지역(0/2/4) + 산업(0~3) + 기업규모(0/1/2) 합산, 최대 9."""
    return regional_score(item.organizer, item.title) + industry_score(item) + size_score(item)


PRIORITY_SCORE_THRESHOLD = 6


def is_priority_match(item: Item, threshold: int = PRIORITY_SCORE_THRESHOLD) -> bool:
    return priority_score(item) >= threshold


def classify_for_curation(
    *,
    items_new: list[Item],
    items_ongoing: list[Item],
    items_expired: list[Item],
    today: date,
) -> list[CurationSection]:
    """Return sections in display order. Empty sections still appear
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

    # 1. 마감임박(0~14일) + 우선조건 동시충족
    for i in pool:
        if i.stable_id in claimed: continue
        if i.deadline and today <= i.deadline <= d14_cutoff and is_priority_match(i):
            _put(SEC_URGENT_PRIORITY, i)

    # 2. 우선조건 충족 (마감 여유 있거나 마감 없음)
    for i in pool:
        if i.stable_id in claimed: continue
        if is_priority_match(i):
            _put(SEC_PRIORITY_MATCH, i)

    # 3. D-5 (긴급, 우선조건 미충족 항목만 남음)
    for i in pool:
        if i.stable_id in claimed: continue
        if i.deadline and today <= i.deadline <= d5_cutoff:
            _put(SEC_DEADLINE_5, i)

    # 4. D-6 ~ D-14
    for i in pool:
        if i.stable_id in claimed: continue
        if i.deadline and (today + timedelta(days=6)) <= i.deadline <= d14_cutoff:
            _put(SEC_DEADLINE_14, i)

    # 5. 신규 + GPU
    for i in pool:
        if i.stable_id in claimed: continue
        if i.stable_id in new_ids and GPU_AI_BADGE in i.badges:
            _put(SEC_NEW_GPU, i)

    # 6. 지역 우선 (부산·경남·경북 — title + organizer 매칭, 우선조건 threshold
    #    미달 항목들의 전용 자리)
    for i in pool:
        if i.stable_id in claimed: continue
        if matched_regions(i.organizer, i.title):
            _put(SEC_REGIONAL, i)

    # 7. GPU 진행중 (위에서 안 잡힌 것)
    for i in pool:
        if i.stable_id in claimed: continue
        if GPU_AI_BADGE in i.badges:
            _put(SEC_GPU_ONGOING, i)

    # 8. 신규 그 외
    for i in pool:
        if i.stable_id in claimed: continue
        if i.stable_id in new_ids:
            _put(SEC_NEW_OTHER, i)

    # 9. 재공고/연장
    for i in pool:
        if i.stable_id in claimed: continue
        if is_reannouncement(i.title):
            _put(SEC_REANNOUNCE, i)

    # 10. 진행중 그 외 (deadline 있음)
    for i in pool:
        if i.stable_id in claimed: continue
        if i.deadline is not None:
            _put(SEC_ONGOING, i)

    # 11. 상시 접수 (deadline=None)
    for i in pool:
        if i.stable_id in claimed: continue
        _put(SEC_ALWAYS_OPEN, i)

    # 12. expired — 전용 pool
    sections[SEC_EXPIRED] = list(items_expired)

    # 각 섹션 내 정렬 (expired 제외 — 모두 deadline ASC).
    for key in sections:
        sections[key].sort(key=_sort_key)

    # 출력 순서대로 CurationSection 객체 빌드.
    order = [
        SEC_URGENT_PRIORITY, SEC_PRIORITY_MATCH,
        SEC_DEADLINE_5, SEC_DEADLINE_14,
        SEC_NEW_GPU, SEC_REGIONAL, SEC_GPU_ONGOING, SEC_NEW_OTHER,
        SEC_REANNOUNCE, SEC_ONGOING, SEC_ALWAYS_OPEN,
        SEC_EXPIRED,
    ]
    result: list[CurationSection] = []
    for key in order:
        title, hdr, brd = SECTION_META[key]
        section_items = sections[key]
        overflow_count = 0
        cap = SECTION_MAX_ITEMS.get(key)
        if cap is not None and len(section_items) > cap:
            overflow_count = len(section_items) - cap
            section_items = section_items[:cap]
        result.append(CurationSection(
            key=key, title=title, header_color=hdr, border_color=brd,
            items=section_items, overflow_count=overflow_count,
        ))
    return result
