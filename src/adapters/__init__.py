"""Adapters convert raw scraper records into the `Item` dataclass that flows
through diff → render → push → mail. One adapter module per archive."""

from __future__ import annotations

from typing import Callable

from ..models import Item


# Registry: archive_key → callable() returning list[Item] for today's scrape.
# Adapters self-register here at import time. The cli scrape stage looks up
# the right adapter per archive.
ADAPTERS: dict[str, Callable[[], list[Item]]] = {}


def register(archive_key: str):
    def deco(fn: Callable[[], list[Item]]) -> Callable[[], list[Item]]:
        ADAPTERS[archive_key] = fn
        return fn
    return deco


# Import side-effects: each adapter module calls @register on import.
from . import gov_support  # noqa: F401, E402
from . import busan_startup  # noqa: F401, E402
from . import kstartup_biz  # noqa: F401, E402
from . import kstartup_mentoring  # noqa: F401, E402
from . import kstartup_global  # noqa: F401, E402
