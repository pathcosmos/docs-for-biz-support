from datetime import date, timedelta

from src.models import Item
from src.render.curation import (
    SEC_DEADLINE_5,
    SEC_MIDSME_AI_MFG_GPU,
    SEC_MIDSME_AI_MFG_GPU_MAX_ITEMS,
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


def test_case_a_new_midsme_ai_item_goes_to_new_section():
    item = _item(
        1,
        title="중소 제조 AI 전환 지원",
        summary="llm 기반 공정 혁신",
        target="중소기업",
    )
    sections = _sections_by_key(items_new=[item], items_ongoing=[], items_expired=[])
    midsme = sections[SEC_MIDSME_AI_MFG_GPU]
    assert [i.stable_id for i in midsme.items] == [item.stable_id]


def test_case_b_claimed_prevents_duplicate_reappearance():
    item = _item(
        2,
        title="중견 GPU 기반 스마트공장 고도화",
        summary="hpc 가속기 도입",
        target="중견기업",
        badges=("🖥️ GPU·AI 인프라",),
    )
    sections = _sections_by_key(items_new=[item], items_ongoing=[], items_expired=[])
    containing = [
        key for key, sec in sections.items() if any(i.stable_id == item.stable_id for i in sec.items)
    ]
    assert containing == [SEC_MIDSME_AI_MFG_GPU]


def test_case_c_deadline5_priority_over_midsme_section():
    item = _item(
        3,
        title="중소 AI 제조 확산",
        summary="gpu 활용",
        target="중소기업",
        deadline=TODAY + timedelta(days=2),
    )
    sections = _sections_by_key(items_new=[item], items_ongoing=[], items_expired=[])
    assert [i.stable_id for i in sections[SEC_DEADLINE_5].items] == [item.stable_id]
    assert sections[SEC_MIDSME_AI_MFG_GPU].items == []


def test_case_d_none_deadline_sorted_last_in_section():
    with_deadline = _item(
        4,
        title="중소 AI 설비 지원",
        target="중소기업",
        deadline=TODAY + timedelta(days=30),
    )
    without_deadline = _item(
        5,
        title="중견 GPU 생산 혁신",
        target="중견기업",
        deadline=None,
    )
    sections = _sections_by_key(
        items_new=[without_deadline, with_deadline], items_ongoing=[], items_expired=[]
    )
    midsme_ids = [i.stable_id for i in sections[SEC_MIDSME_AI_MFG_GPU].items]
    assert midsme_ids == [with_deadline.stable_id, without_deadline.stable_id]


def test_case_e_cap_and_overflow_notice():
    items = [
        _item(
            i,
            title=f"중소 AI 제조 GPU 과제 {i}",
            summary="llm gpu 스마트공장",
            target="중소기업",
            deadline=TODAY + timedelta(days=20 + i),
        )
        for i in range(100, 100 + SEC_MIDSME_AI_MFG_GPU_MAX_ITEMS + 3)
    ]
    sections = _sections_by_key(items_new=items, items_ongoing=[], items_expired=[])
    midsme = sections[SEC_MIDSME_AI_MFG_GPU]
    assert len(midsme.items) == SEC_MIDSME_AI_MFG_GPU_MAX_ITEMS
    assert midsme.overflow_count == 3

    html = _render_curation_section(midsme, TODAY)
    assert "+3건 더 있음" in html
