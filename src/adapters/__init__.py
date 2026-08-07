"""Adapters convert raw scraper records into the `Item` dataclass that flows
through diff → render → push → mail. One adapter module per archive."""

from __future__ import annotations

from collections.abc import Callable

from ..models import AdapterResult

# Registry: archive_key → callable() returning list[Item] for today's scrape.
# Adapters self-register here at import time. The cli scrape stage looks up
# the right adapter per archive.
ADAPTERS: dict[str, Callable[[], AdapterResult]] = {}


def register(archive_key: str):
    def deco(fn: Callable[[], AdapterResult]) -> Callable[[], AdapterResult]:
        ADAPTERS[archive_key] = fn
        return fn
    return deco


# Import side-effects: each adapter module calls @register on import.
from . import (
    busan_startup,  # noqa: F401
    gov_support,  # noqa: F401
    kstartup_biz,  # noqa: F401
    kstartup_global,  # noqa: F401
    kstartup_mentoring,  # noqa: F401
)
