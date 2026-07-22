from datetime import date, timedelta

from src.models import Item
from src.render import curation
from src.render.curation import (
    SEC_DEADLINE_5,
    SEC_DEADLINE_14,
    SEC_PRIORITY_MATCH,
    SEC_REGIONAL,
    SEC_URGENT_PRIORITY,
    SECTION_MAX_ITEMS,
    classify_for_curation,
)
from src.render.daily_html import _render_curation_section


TODAY = date(2026, 5, 27)


def _item(idx: int, **kwargs) -> Item:
    base = dict(
        stable_id=f"id-{idx}",
        source_key="bizinfo",
        title=f"title-{idx}",
        detail_url=f"https://example.com/{idx}",
    )
    base.update(kwargs)
    return Item(**base)


def _sections_by_key(**kwargs):
    sections = classify_for_curation(today=TODAY, **kwargs)
    return {s.key: s for s in sections}


def test_case_a_new_priority_item_goes_to_priority_section():
    item = _item(
        1,
        title="중견 제조 AI 지원사업",
        summary="스마트공장",
        organizer="부산광역시",
        target="중견기업",
    )
    sections = _sections_by_key(items_new=[item], items_ongoing=[], items_expired=[])
    priority = sections[SEC_PRIORITY_MATCH]
    assert [i.stable_id for i in priority.items] == [item.stable_id]


def test_case_b_claimed_prevents_duplicate_reappearance():
    item = _item(
        2,
        title="중견 GPU AI 기반 스마트공장 고도화",
        summary="hpc 가속기 도입",
        organizer="경상남도",
        target="중견기업",
        badges=("🖥️ GPU·AI 인프라",),
    )
    sections = _sections_by_key(items_new=[item], items_ongoing=[], items_expired=[])
    containing = [
        key for key, sec in sections.items() if any(i.stable_id == item.stable_id for i in sec.items)
    ]
    # 우선조건(SEC_PRIORITY_MATCH)과 신규+GPU(SEC_NEW_GPU) 둘 다 매칭되지만
    # claimed-set 선점으로 먼저 도는 SEC_PRIORITY_MATCH 하나에만 들어간다.
    assert containing == [SEC_PRIORITY_MATCH]


def test_case_c1_deadline5_wins_when_priority_criteria_not_met():
    """우선조건(threshold=6)을 못 넘는 D-5 항목은 여전히 SEC_DEADLINE_5로 간다
    — 재구성 이전 동작에 대한 회귀 가드."""
    item = _item(
        3,
        title="AI 활용 지원사업",
        deadline=TODAY + timedelta(days=2),
    )
    assert curation.priority_score(item) < curation.PRIORITY_SCORE_THRESHOLD
    sections = _sections_by_key(items_new=[item], items_ongoing=[], items_expired=[])
    assert [i.stable_id for i in sections[SEC_DEADLINE_5].items] == [item.stable_id]
    assert sections[SEC_URGENT_PRIORITY].items == []
    assert sections[SEC_PRIORITY_MATCH].items == []


def test_case_c2_urgent_priority_wins_over_deadline5_when_criteria_met():
    """우선조건을 넘는 D-5 항목은 이제 SEC_URGENT_PRIORITY로 간다 — 이번
    재구성의 핵심 동작 변경 (사용자 요청: 마감임박보다 우선조건 상위 배치)."""
    item = _item(
        4,
        title="중소 AI 제조 확산",
        organizer="부산광역시",
        target="중소기업",
        deadline=TODAY + timedelta(days=2),
    )
    assert curation.priority_score(item) >= curation.PRIORITY_SCORE_THRESHOLD
    sections = _sections_by_key(items_new=[item], items_ongoing=[], items_expired=[])
    assert [i.stable_id for i in sections[SEC_URGENT_PRIORITY].items] == [item.stable_id]
    assert sections[SEC_DEADLINE_5].items == []
    assert sections[SEC_PRIORITY_MATCH].items == []


def test_case_c3_urgent_priority_covers_full_0_to_14_day_window():
    """Tier①은 기존 D-5/D-14 두 구간을 하나로 묶는다 — D-6~14 구간의
    우선조건 매칭 항목도 SEC_DEADLINE_14가 아니라 SEC_URGENT_PRIORITY로."""
    item = _item(
        5,
        title="경남 중견 제조 AI GPU 혁신사업",
        organizer="경상남도",
        deadline=TODAY + timedelta(days=10),
    )
    assert curation.priority_score(item) >= curation.PRIORITY_SCORE_THRESHOLD
    sections = _sections_by_key(items_new=[item], items_ongoing=[], items_expired=[])
    assert [i.stable_id for i in sections[SEC_URGENT_PRIORITY].items] == [item.stable_id]
    assert sections[SEC_DEADLINE_14].items == []


def test_case_d_none_deadline_sorted_last_in_section():
    with_deadline = _item(
        6,
        title="중소 AI 설비 지원",
        organizer="부산광역시",
        target="중소기업",
        deadline=TODAY + timedelta(days=30),
    )
    without_deadline = _item(
        7,
        title="중견 AI 생산 혁신",
        organizer="경상남도",
        target="중견기업",
        deadline=None,
    )
    sections = _sections_by_key(
        items_new=[without_deadline, with_deadline], items_ongoing=[], items_expired=[]
    )
    priority_ids = [i.stable_id for i in sections[SEC_PRIORITY_MATCH].items]
    assert priority_ids == [with_deadline.stable_id, without_deadline.stable_id]


def test_case_e_priority_match_cap_and_overflow_notice():
    cap = SECTION_MAX_ITEMS[SEC_PRIORITY_MATCH]
    items = [
        _item(
            i,
            title=f"중소 AI 제조 과제 {i}",
            summary="llm 스마트공장",
            organizer="부산광역시",
            target="중소기업",
            deadline=TODAY + timedelta(days=20 + i),  # D-14 밖 → Tier2로만 감
        )
        for i in range(100, 100 + cap + 3)
    ]
    sections = _sections_by_key(items_new=items, items_ongoing=[], items_expired=[])
    priority = sections[SEC_PRIORITY_MATCH]
    assert len(priority.items) == cap
    assert priority.overflow_count == 3

    html = _render_curation_section(priority, TODAY)
    assert "+3건 더 있음" in html


def test_case_f_organizer_regional_match_weighted_higher_than_title_only():
    """regional_score: organizer 매칭(+4)이 title 매칭(+2)보다 신뢰도가 높다는
    가중치 설계가 threshold 경계에서 실제로 결과를 가른다."""
    organizer_match = _item(
        8,
        title="AI 활용 지원사업",
        organizer="경남테크노파크",  # 지역 신호는 organizer에서만 옴 (+4)
    )
    title_only_match = _item(
        9,
        title="경남 AI 활용 지원사업",  # 지역 신호는 title에서만 옴 (+2)
    )
    assert curation.priority_score(organizer_match) >= curation.PRIORITY_SCORE_THRESHOLD
    assert curation.priority_score(title_only_match) < curation.PRIORITY_SCORE_THRESHOLD

    sections = _sections_by_key(
        items_new=[organizer_match, title_only_match], items_ongoing=[], items_expired=[]
    )
    assert [i.stable_id for i in sections[SEC_PRIORITY_MATCH].items] == [organizer_match.stable_id]


def test_case_g_pure_regional_match_falls_through_to_regional_section():
    """산업/규모 신호 없이 지역만 매칭되면(4점) threshold(6)를 못 넘어 우선
    섹션엔 못 들어가고, 지역 전용 섹션(SEC_REGIONAL)에만 들어간다 — SEC_BUSAN을
    삭제하지 않고 넓혀서 유지한 이유에 대한 회귀 가드."""
    item = _item(
        10,
        title="일반 창업 지원사업",
        organizer="부산광역시",
    )
    assert curation.priority_score(item) < curation.PRIORITY_SCORE_THRESHOLD
    sections = _sections_by_key(items_new=[item], items_ongoing=[], items_expired=[])
    assert [i.stable_id for i in sections[SEC_REGIONAL].items] == [item.stable_id]
    assert sections[SEC_URGENT_PRIORITY].items == []
    assert sections[SEC_PRIORITY_MATCH].items == []


def test_case_h_urgent_priority_is_uncapped():
    items = [
        _item(
            i,
            title=f"경남 중견 제조 AI GPU 긴급과제 {i}",
            organizer="경상남도",
            deadline=TODAY + timedelta(days=3),
        )
        for i in range(200, 260)  # SEC_PRIORITY_MATCH cap(50)보다 많은 60건
    ]
    sections = _sections_by_key(items_new=items, items_ongoing=[], items_expired=[])
    urgent = sections[SEC_URGENT_PRIORITY]
    assert len(urgent.items) == 60
    assert urgent.overflow_count == 0


def test_case_i_regional_section_cap_and_overflow_notice():
    cap = SECTION_MAX_ITEMS[SEC_REGIONAL]
    items = [
        _item(i, title=f"부산 일반 지원사업 {i}", organizer="부산광역시")
        for i in range(300, 300 + cap + 3)
    ]
    sections = _sections_by_key(items_new=items, items_ongoing=[], items_expired=[])
    regional = sections[SEC_REGIONAL]
    assert len(regional.items) == cap
    assert regional.overflow_count == 3

    html = _render_curation_section(regional, TODAY)
    assert "+3건 더 있음" in html


def test_case_k_gpu_tokens_do_not_count_toward_priority():
    """GPU 신호는 우선조건 스코어에서 제외 — GPU 항목은 전용 섹션(badge 기반)이
    따로 잡는다. 예전 스코어링(GPU +2)이라면 6점(중견2+GPU2+α)으로 우선조건에
    들어가던 조합이 이제 threshold 미달로 GPU 섹션에만 남는지 확인."""
    item = _item(
        11,
        title="중견 GPU 인프라 지원사업",
        summary="gpu npu hpc 가속기 고성능컴퓨팅",
        target="중견기업",
        badges=("🖥️ GPU·AI 인프라",),
    )
    assert curation.priority_score(item) < curation.PRIORITY_SCORE_THRESHOLD
    sections = _sections_by_key(items_new=[item], items_ongoing=[], items_expired=[])
    assert sections[SEC_PRIORITY_MATCH].items == []
    assert sections[SEC_URGENT_PRIORITY].items == []
    assert [i.stable_id for i in sections[curation.SEC_NEW_GPU].items] == [item.stable_id]


def test_case_j_retired_section_keys_are_gone():
    """옛 섹션(SEC_MIDSME_AI_MFG_GPU/SEC_BUSAN)이 부분 revert로 되살아나지
    않도록 하는 가드."""
    assert not hasattr(curation, "SEC_MIDSME_AI_MFG_GPU")
    assert not hasattr(curation, "SEC_BUSAN")
    assert not hasattr(curation, "SEC_MIDSME_AI_MFG_GPU_MAX_ITEMS")
    assert "midsme_ai_mfg_gpu" not in curation.SECTION_META
    assert "busan" not in curation.SECTION_META
