"""Keyword → badge label mapping. Lets adapters tag items with curated badges
based on title / summary / hashtags text. Used for the GPU·AI infrastructure
curation (`🖥️ GPU·AI 인프라` badge surfaces items whose detail text mentions
GPU/클라우드/AI infrastructure terms) and for regional tagging (`🗺️ 부산` /
`🗺️ 경남` / `🗺️ 경북`, via `matched_regions`/`regional_score`, consumed by
`curation.py`'s priority-score sections).

The dictionary is intentionally conservative — false positives (a 가족돌봄
사업 that happens to mention "AI" in a tangential sentence) clutter the GPU
section. Add new terms only after seeing the actual production output."""

from __future__ import annotations

import re


GPU_AI_BADGE = "🖥️ GPU·AI 인프라"

# Keywords are case-insensitive; matched as whole substrings in the merged
# search text (title + summary + organizer + hashtags joined by spaces).
# Korean tokens don't need word boundaries — they're not Latin-letter words.
# Latin tokens use word-boundary patterns to avoid matching 'AI' inside
# unrelated words.
_KOREAN_TOKENS: tuple[str, ...] = (
    "GPU",
    "엔비디아",
    "클라우드",
    "AI 인프라",
    "AI인프라",
    "AI 컴퓨팅",
    "AI컴퓨팅",
    "AI 학습",
    "AI학습",
    "NPU",
    "TPU",
    "학습 인프라",
    "데이터센터",
    "컴퓨팅 자원",
    "컴퓨팅자원",
    "고성능 컴퓨팅",
    "HPC",
    "AX",
)

# Latin-letter tokens that need word boundaries.
_LATIN_TOKENS: tuple[str, ...] = (
    r"\bAI\b",
    r"\bGPU\b",
    r"\bLLM\b",
    r"\bSLM\b",
    r"\bMLOps\b",
)

# Compiled regex: union of (escaped Korean tokens) | (Latin token regex).
_PATTERN = re.compile(
    "|".join(
        [re.escape(t) for t in _KOREAN_TOKENS] +
        list(_LATIN_TOKENS)
    ),
    flags=re.IGNORECASE,
)


def is_gpu_ai_infra(text: str | None) -> bool:
    """Return True if any GPU/AI keyword matches in `text`. None/empty → False."""
    if not text:
        return False
    return _PATTERN.search(text) is not None


# ── 지역 매처 (부산·경남·경북) ──────────────────────────────────────────────
# title + organizer 에서 지역명 또는 산하 시·군·구/기관명을 찾는다. Plan 결정에
# 따라 광역으로 잡되 false positive(예: 부산은행 같은 회사명)는 운영 후
# 사용자 피드백으로 조정. organizer 가 '부산광역시'/'경상남도'/'경북테크노파크'
# 같은 공식 기관명이면 신뢰도 높음 — PR-PRIORITY의 `regional_score`가 이
# organizer-매칭을 title-매칭보다 높게 가중하는 이유.

_BUSAN_TOKENS: tuple[str, ...] = (
    "부산",
    "해운대", "기장", "서면", "동래", "연제", "사하", "부산진", "영도",
    "중구·동구", "사상", "남구·수영",  # 부산의 행정구
    "센텀", "B.Cube", "B-cube", "B큐브",  # 부산 창업 시설 명
)
_GYEONGNAM_TOKENS: tuple[str, ...] = (
    "경상남도", "경남", "창원", "진주", "김해", "양산", "거제", "통영", "사천",
    "밀양", "거창", "합천", "함안", "창녕", "고성", "남해", "하동", "산청",
    "의령", "함양",  # 경남의 시·군
    "경남지방중소벤처기업청", "경남테크노파크",
)
_GYEONGBUK_TOKENS: tuple[str, ...] = (
    "경상북도", "경북", "포항", "구미", "경주", "안동", "김천", "영주", "영천",
    "상주", "문경", "경산", "군위", "의성", "청송", "영양", "영덕", "청도",
    "고령", "성주", "칠곡", "예천", "봉화", "울진", "울릉",  # 경북의 시·군
    "경북지방중소벤처기업청", "경북테크노파크",
)
_BUSAN_PATTERN = re.compile(
    "|".join(re.escape(t) for t in _BUSAN_TOKENS), flags=re.IGNORECASE,
)
_GYEONGNAM_PATTERN = re.compile(
    "|".join(re.escape(t) for t in _GYEONGNAM_TOKENS), flags=re.IGNORECASE,
)
_GYEONGBUK_PATTERN = re.compile(
    "|".join(re.escape(t) for t in _GYEONGBUK_TOKENS), flags=re.IGNORECASE,
)

_REGION_PATTERNS: dict[str, re.Pattern[str]] = {
    "busan": _BUSAN_PATTERN,
    "gyeongnam": _GYEONGNAM_PATTERN,
    "gyeongbuk": _GYEONGBUK_PATTERN,
}
REGION_BADGES: dict[str, str] = {
    "busan": "🗺️ 부산",
    "gyeongnam": "🗺️ 경남",
    "gyeongbuk": "🗺️ 경북",
}


def is_busan(*texts: str | None) -> bool:
    """텍스트들(title, organizer 등) 중 하나라도 부산 키워드가 매칭되면 True."""
    for t in texts:
        if t and _BUSAN_PATTERN.search(t):
            return True
    return False


def is_gyeongnam(*texts: str | None) -> bool:
    """텍스트들 중 하나라도 경남 키워드가 매칭되면 True."""
    for t in texts:
        if t and _GYEONGNAM_PATTERN.search(t):
            return True
    return False


def is_gyeongbuk(*texts: str | None) -> bool:
    """텍스트들 중 하나라도 경북 키워드가 매칭되면 True."""
    for t in texts:
        if t and _GYEONGBUK_PATTERN.search(t):
            return True
    return False


def matched_regions(organizer: str | None, title: str | None) -> tuple[str, ...]:
    """organizer 또는 title에서 매칭된 지역 키(들)을 안정적인 순서로 반환.
    ('busan', 'gyeongnam', 'gyeongbuk') 순서 고정, 매칭 없으면 빈 튜플."""
    out: list[str] = []
    for key, pattern in _REGION_PATTERNS.items():
        if (organizer and pattern.search(organizer)) or (title and pattern.search(title)):
            out.append(key)
    return tuple(out)


def regional_score(organizer: str | None, title: str | None) -> int:
    """organizer 매칭이 title 매칭보다 신뢰도가 높다 (organizer는 거의 100%
    채워지는 반면 title 매칭은 우연한 언급일 수 있음) — PR-PRIORITY 가중치.
    organizer 매칭 시 4점, organizer는 안 맞고 title만 맞으면 2점, 둘 다
    없으면 0점."""
    if organizer and any(p.search(organizer) for p in _REGION_PATTERNS.values()):
        return 4
    if title and any(p.search(title) for p in _REGION_PATTERNS.values()):
        return 2
    return 0


# ── 재공고/연장 매처 ─────────────────────────────────────────────────────
# 동일 사업이 마감 연장되어 다시 올라온 경우. title 에 명시되는 한국어
# 키워드를 보수적으로만 잡는다 (단순 '연장' 한 단어는 일반 사업명에도 흔히
# 등장 → 더 구체적인 phrase 만 매칭).

_REANNOUNCE_TOKENS: tuple[str, ...] = (
    "재공고",
    "재모집",
    "추가모집",
    "추가공고",
    "기간연장",
    "기간 연장",
    "모집연장",
    "모집 연장",
    "공고연장",
    "공고 연장",
)
_REANNOUNCE_PATTERN = re.compile("|".join(re.escape(t) for t in _REANNOUNCE_TOKENS))


def is_reannouncement(title: str | None) -> bool:
    """제목에 재공고/연장 관련 phrase 가 있으면 True."""
    if not title:
        return False
    return _REANNOUNCE_PATTERN.search(title) is not None


def assign_badges(
    *,
    title: str | None,
    summary: str | None,
    hashtags: list[str] | None = None,
    organizer: str | None = None,
    existing: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Return badges including any keyword-driven additions. Preserves
    `existing` badges and de-dupes."""
    parts: list[str] = []
    if title: parts.append(title)
    if summary: parts.append(summary)
    if hashtags: parts.extend(hashtags)
    if organizer: parts.append(organizer)
    haystack = " ".join(parts)

    result = list(existing)
    if is_gpu_ai_infra(haystack) and GPU_AI_BADGE not in result:
        result.append(GPU_AI_BADGE)
    for region_key in matched_regions(organizer, title):
        badge = REGION_BADGES[region_key]
        if badge not in result:
            result.append(badge)
    return tuple(result)
