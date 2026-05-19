"""Keyword → badge label mapping. Lets adapters tag items with curated badges
based on title / summary / hashtags text. Used primarily for the GPU·AI
infrastructure curation: `🖥️ GPU·AI 인프라` badge surfaces items whose detail
text mentions GPU/클라우드/AI infrastructure terms.

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


# ── 부산 매처 ──────────────────────────────────────────────────────────────
# title + organizer 에서 '부산' 또는 부산 산하 구·군 명을 찾는다. Plan 결정에
# 따라 광역으로 잡되 false positive(예: 부산은행 같은 회사명)는 첫 운영 후
# 사용자 피드백으로 조정. organizer 가 '부산광역시' 또는 '부산기술창업…'
# 같은 공식 기관명이면 신뢰도 높음.

_BUSAN_TOKENS: tuple[str, ...] = (
    "부산",
    "해운대", "기장", "서면", "동래", "연제", "사하", "부산진", "영도",
    "중구·동구", "사상", "남구·수영",  # 부산의 행정구
    "센텀", "B.Cube", "B-cube", "B큐브",  # 부산 창업 시설 명
)
_BUSAN_PATTERN = re.compile("|".join(re.escape(t) for t in _BUSAN_TOKENS))


def is_busan(*texts: str | None) -> bool:
    """텍스트들(title, organizer 등) 중 하나라도 부산 키워드가 매칭되면 True."""
    for t in texts:
        if t and _BUSAN_PATTERN.search(t):
            return True
    return False


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
    return tuple(result)
