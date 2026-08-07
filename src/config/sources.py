from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


@dataclass(frozen=True)
class SourceDef:
    """Static config per source. `display` is the exact Korean text rendered in
    the source-tag pill on each item card. `id_rule` extracts the stable id from
    the item's detail URL; returns None if it can't, in which case the adapter
    falls back to sha1(normalized_url) and emits a warning."""

    key: str
    display: str
    id_rule: Callable[[str], str | None]


def _qs(url: str, *names: str) -> str | None:
    """Return the first query-string param whose name matches one of `names`,
    or None. Multi-value params get the first occurrence."""
    qs = parse_qs(urlparse(url).query)
    for n in names:
        if qs.get(n):
            return qs[n][0]
    return None


def _smes_id(url: str) -> str | None:
    # smes.go.kr uses NTTSN for notices; some pages also expose BBSCLCODESE.
    return _qs(url, "NTTSN")


def _smart_factory_id(url: str) -> str | None:
    pbanc_id = _qs(url, "pbancId")
    pbanc_sn = _qs(url, "pbancSn")
    if pbanc_id and pbanc_sn:
        return f"{pbanc_id}-{pbanc_sn}"
    return pbanc_id


def _kstartup_id(url: str) -> str | None:
    return _qs(url, "id")


def _numeric_path_tail(url: str) -> str | None:
    """Fallback for sites where the id lives at the end of the URL path
    (e.g. busanstartup.kr/uRent?... or jntp/announcement=NNN)."""
    query_id = _qs(url, "announcement", "ri_idx", "id")
    if query_id:
        return query_id
    tail = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return tail if tail.isdigit() else None


SOURCES: dict[str, SourceDef] = {
    # Active government archive sources
    "bizinfo":     SourceDef("bizinfo",     "기업마당",         lambda u: _qs(u, "pblancId")),
    "ntis":        SourceDef("ntis",        "NTIS 국가R&D",     lambda u: _qs(u, "roRndUid")),
    "iris":        SourceDef("iris",        "IRIS 범부처R&D",   lambda u: _qs(u, "ancmId")),
    "nipa":        SourceDef("nipa",        "NIPA",            lambda u: _qs(u, "bsnsDtlsIemNo")),

    # Historical archive labels retained for seed/import compatibility. They
    # are not active coverage unless listed in ArchiveConfig.sources.
    "smes":        SourceDef("smes",        "중소벤처24",       _smes_id),
    "smart_factory": SourceDef("smart_factory", "스마트공장",   _smart_factory_id),
    "cbtp":        SourceDef("cbtp",        "cbtp",            _numeric_path_tail),
    "djtp":        SourceDef("djtp",        "djtp",            _numeric_path_tail),
    "gbtp":        SourceDef("gbtp",        "gbtp",            _numeric_path_tail),
    "jntp":        SourceDef("jntp",        "jntp",            _numeric_path_tail),
    "utp":         SourceDef("utp",         "utp",             _numeric_path_tail),
    "btp":         SourceDef("btp",         "부산테크노파크",   _numeric_path_tail),

    # busan-startup-archive sources
    "busan_startup": SourceDef("busan_startup", "부산창업지원", _numeric_path_tail),
    "busan_service": SourceDef("busan_service", "부산창업 서비스", _numeric_path_tail),

    # All three K-Startup archives share one display name; the stable-id rule
    # is identical (`id` query param) because all three endpoints use the same
    # URL shape webXXX.do?schM=view&id=NNNNN.
    "kstartup_biz":       SourceDef("kstartup_biz",       "K-Startup 사업소개", _kstartup_id),
    "kstartup_mentoring": SourceDef("kstartup_mentoring", "K-Startup 사업소개", _kstartup_id),
    "kstartup_global":    SourceDef("kstartup_global",    "K-Startup 사업소개", _kstartup_id),
}
