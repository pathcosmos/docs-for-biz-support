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
