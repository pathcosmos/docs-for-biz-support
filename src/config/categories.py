from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str
    emoji: str
    name: str
    color: str  # hex string '#xxxxxx' — both header bg and item-card border-left


# Categories used by archives that group their 진행 중 section. The list ORDER
# is the render order; do not alphabetize. Colors and Korean names are taken
# verbatim from the existing 2026-05-13.html outputs in the five archive repos.

CATEGORIES_BUSAN_STARTUP: tuple[Category, ...] = (
    Category("education", "📚", "창업교육",         "#1565c0"),
    Category("mentoring",  "🧭", "멘토링·컨설팅",     "#00897b"),
    Category("commercialization", "🚀", "사업화",     "#2e7d32"),
    Category("policy_fund","💰", "정책자금",         "#c62828"),
    Category("rnd",        "🔬", "R&D",            "#7b1fa2"),
    Category("facility",   "🏢", "시설·공간",         "#4527a0"),
    Category("global",     "🌏", "판로·해외진출",     "#0277bd"),
    Category("networking", "🤝", "행사·네트워크",     "#ad1457"),
    Category("consulting", "🧭", "컨설팅/멘토링",     "#00695c"),
    Category("residency",  "🏠", "입주지원",         "#37474f"),
)

CATEGORIES_KSTARTUP_MENTORING: tuple[Category, ...] = (
    Category("rnd",        "🔬", "R&D",            "#7b1fa2"),
    Category("mentoring",  "🧭", "멘토링/컨설팅",     "#00897b"),
)

CATEGORIES_KSTARTUP_GLOBAL: tuple[Category, ...] = (
    Category("facility",   "🏢", "시설/공간",         "#4527a0"),
    Category("global",     "🌏", "글로벌",            "#0277bd"),
)
