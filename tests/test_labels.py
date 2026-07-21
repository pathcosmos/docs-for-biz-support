from src.render.labels import (
    GPU_AI_BADGE,
    REGION_BADGES,
    assign_badges,
    is_busan,
    is_gyeongbuk,
    is_gyeongnam,
    matched_regions,
    regional_score,
)


def test_is_busan_matches_district_and_org_name():
    assert is_busan("해운대 창업 지원사업", None)
    assert is_busan(None, "부산광역시")
    assert not is_busan("서울 지원사업", "서울산업진흥원")


def test_is_gyeongnam_matches_city_and_org_name():
    assert is_gyeongnam("김해 스마트공장 지원", None)
    assert is_gyeongnam(None, "경남테크노파크")
    assert not is_gyeongnam("서울 지원사업", "서울산업진흥원")


def test_is_gyeongbuk_matches_city_and_org_name():
    assert is_gyeongbuk("포항 이차전지 지원", None)
    assert is_gyeongbuk(None, "경상북도")
    assert not is_gyeongbuk("서울 지원사업", "서울산업진흥원")


def test_matched_regions_organizer_and_title():
    assert matched_regions("부산광역시", None) == ("busan",)
    assert matched_regions(None, "경남 스마트공장 지원") == ("gyeongnam",)
    assert matched_regions("서울산업진흥원", "서울 지원사업") == ()


def test_matched_regions_stable_order_and_no_duplicates():
    # organizer는 경북, title은 부산 — 둘 다 매칭되면 고정 순서(busan, gyeongnam,
    # gyeongbuk)로 반환.
    assert matched_regions("경상북도", "부산 공동 사업") == ("busan", "gyeongbuk")


def test_regional_score_weights_organizer_over_title():
    # organizer 매칭 → 4점 (title에 지역어가 없어도).
    assert regional_score("경남테크노파크", "AI 활용 지원사업") == 4
    # organizer는 매칭 안 되고 title만 매칭 → 2점.
    assert regional_score(None, "경남 AI 활용 지원사업") == 2
    assert regional_score("서울산업진흥원", "서울 지원사업") == 0
    assert regional_score(None, None) == 0


def test_assign_badges_attaches_regional_pill():
    badges = assign_badges(title="김해 스마트공장 지원사업", summary=None, organizer=None)
    assert REGION_BADGES["gyeongnam"] in badges


def test_assign_badges_dedupes_gpu_and_region_badges():
    badges = assign_badges(
        title="부산 GPU 인프라 지원사업",
        summary="AI 학습 인프라 구축",
        organizer="부산광역시",
        existing=(GPU_AI_BADGE, REGION_BADGES["busan"]),
    )
    assert badges.count(GPU_AI_BADGE) == 1
    assert badges.count(REGION_BADGES["busan"]) == 1


def test_assign_badges_multi_region_both_pills_attached():
    badges = assign_badges(title="부산-경남 공동 창업 지원사업", summary=None, organizer=None)
    assert REGION_BADGES["busan"] in badges
    assert REGION_BADGES["gyeongnam"] in badges


def test_assign_badges_nipa_organizer_never_matches_region():
    # NIPA 어댑터가 organizer="NIPA"로 고정 세팅하는 케이스 — 지역 매칭 없음.
    badges = assign_badges(title="AI 바우처 지원사업", summary=None, organizer="NIPA")
    assert not any(b in badges for b in REGION_BADGES.values())
