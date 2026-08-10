"""기업마당 (bizinfo.go.kr) support-program scraper.

Collection order:

1. Authenticated official JSON API when ``BIZINFO_API_KEY`` is configured.
2. Public official Excel download, which returns authoritative application
   dates for the same complete list in one request. When both succeed, Excel
   supplies list/date fields and the API supplies summary/target/hashtags.
3. The paginated HTML list on both ``www`` and bare hosts.

The HTML path remains a last live fallback. Each page is server-rendered with
a single `<table>` inside `<div class="table_Type_1">` and 8 columns:

    번호 | 지원분야 | 사업명+링크 | 신청기간 | 소관부처·지자체 | 사업수행기관 | 등록일 | 조회수

The link in column 3 carries `pblancId=PBLN_NNNNNNNNNNNN` (12 zero-padded
digits) which is the stable identifier used in legacy archives.

The adapter adds a completed DB snapshot fallback after all live paths fail.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import date

from bs4 import BeautifulSoup, Tag

from .base import HttpClient, ScrapeError

logger = logging.getLogger(__name__)

BASE = "https://www.bizinfo.go.kr"
BASE_MIRRORS = (BASE, "https://bizinfo.go.kr")
LIST_PATH = "/sii/siia/selectSIIA200View.do"
DETAIL_PATH = "/sii/siia/selectSIIA200Detail.do"
EXCEL_PATH = "/sii/siia/selectSIIA200ExcelDownload.do"
API_PATH = "/uss/rss/bizinfoApi.do"
MAX_PAGES = 200    # 15/page × 200 = 3000 ceiling — plenty

# GitHub-hosted runner measurements on 2026-08-08 established a clear boundary:
# every 10-second connection attempt timed out, while otherwise-identical
# 20-second and 30-second dry runs both completed a fresh 1,572-item aggregate
# without cache fallback. Keep 50% headroom over the measured success boundary.
LIST_PAGE_TIMEOUT = 30.0
MAX_LIST_PAGE_TIMEOUT = 120.0
OUTAGE_RETRY_DELAY_SECONDS = 30.0
BACKUP_HOST_ATTEMPTS = 1

_ORIGIN_UNREACHABLE_KINDS = {
    "ConnectError",
    "ConnectTimeout",
    "NetworkError",
    "ProxyError",
}


@dataclass(frozen=True)
class BizinfoListResult:
    items: list[BizinfoRaw]
    complete: bool   # False if pagination stopped early due to a page failure
    channel: str = "html"


@dataclass(frozen=True)
class BizinfoRaw:
    pblanc_id: str                  # 'PBLN_000000000122138'
    title: str
    apply_period: str | None
    deadline: date | None
    organizer: str | None           # 소관부처·지자체 (col 5)
    executor: str | None            # 사업수행기관 (col 6) — kept separate
    support_field: str | None       # 지원분야 (col 2) — e.g. '내수', '창업'
    summary: str | None = None
    target: str | None = None
    hashtags: tuple[str, ...] = ()


_DATE_RE = re.compile(r"(\d{4})[-./]?(\d{2})[-./]?(\d{2})")
_PBLANC_RE = re.compile(r"pblancId=(PBLN_\d+)")
_XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_EXCEL_HEADERS = {
    "소관부처", "사업수행기관", "지원분야", "공고명",
    "신청시작일자", "신청종료일자", "공고상세URL",
}


def fetch_listings(client: HttpClient) -> BizinfoListResult:
    """Fetch a complete list through the strongest available official path."""
    failures: list[str] = []
    live_errors: list[ScrapeError] = []
    api_items: list[BizinfoRaw] | None = None
    list_page_timeout = _list_page_timeout()

    api_key = os.environ.get("BIZINFO_API_KEY", "").strip()
    if api_key:
        try:
            items = _fetch_official_api(client, api_key)
        except ScrapeError as e:
            failures.append(f"api: {e}")
            logger.warning("bizinfo official API failed, trying Excel: %s", e)
        else:
            logger.info("bizinfo: %d items collected through official API", len(items))
            api_items = items
    elif os.environ.get("GITHUB_ACTIONS") == "true":
        logger.warning(
            "bizinfo official API disabled: BIZINFO_API_KEY is not configured",
        )

    excel_result = _fetch_excel_from_hosts(
        client,
        failures,
        live_errors,
        label="excel",
        timeout=list_page_timeout,
        hosts=(BASE,) if api_items is not None else BASE_MIRRORS,
        primary_attempts=1 if api_items is not None else None,
    )
    if excel_result:
        if api_items is not None:
            merged = _merge_api_enrichment(excel_result.items, api_items)
            logger.info(
                "bizinfo: %d Excel items enriched through official API", len(merged),
            )
            return BizinfoListResult(items=merged, complete=True, channel="api+excel")
        return excel_result
    if api_items is not None:
        # The API result has already passed total-count and unique-ID checks.
        # Prefer it to stale cache if the optional Excel date enrichment is
        # unavailable; textual periods may be less precise, but the live list
        # itself remains complete.
        logger.warning(
            "bizinfo Excel date enrichment failed; using complete API result",
        )
        return BizinfoListResult(items=api_items, complete=True, channel="api")

    excel_errors = live_errors.copy()
    origin_unreachable = (
        len(excel_errors) == len(BASE_MIRRORS)
        and all(_is_origin_unreachable(error) for error in excel_errors)
    )
    if origin_unreachable:
        # Both aliases currently terminate at the same Bizinfo origin. When
        # the one-request Excel path cannot even connect on either name, a
        # full HTML request through the same origin only repeats the outage
        # and can consume another several minutes of the workflow budget.
        failures.append("html: skipped because every Excel host was unreachable")
        logger.warning(
            "bizinfo HTML fallback skipped: every Excel host was unreachable",
        )
    else:
        try:
            return _fetch_html_listings(client, timeout=list_page_timeout)
        except ScrapeError as e:
            failures.append(f"html: {e}")
            live_errors.append(e)

    # A connection timeout or DNS failure means the shared origin was
    # temporarily unreachable. One delayed Excel retry gives that origin time
    # to recover. Parse/schema failures are deterministic and should fall back
    # to cache immediately instead.
    if any(_is_origin_unreachable(error) for error in live_errors):
        logger.warning(
            "bizinfo origin unreachable; retrying Excel after %.0fs",
            OUTAGE_RETRY_DELAY_SECONDS,
        )
        time.sleep(OUTAGE_RETRY_DELAY_SECONDS)
        excel_result = _fetch_excel_from_hosts(
            client,
            failures,
            live_errors,
            label="delayed excel",
            timeout=list_page_timeout,
            hosts=(BASE,),
        )
        if excel_result:
            return excel_result

    raise ScrapeError("bizinfo all live collection paths failed: " + " | ".join(failures))


def _fetch_excel_from_hosts(
    client: HttpClient,
    failures: list[str],
    errors: list[ScrapeError],
    *,
    label: str,
    timeout: float,
    hosts: tuple[str, ...] = BASE_MIRRORS,
    primary_attempts: int | None = None,
) -> BizinfoListResult | None:
    for host_index, base in enumerate(hosts):
        try:
            payload = client.get_bytes(
                f"{base}{EXCEL_PATH}",
                params={"1": "1", "schEndAt": "N"},
                timeout=timeout,
                attempts=primary_attempts if host_index == 0 else BACKUP_HOST_ATTEMPTS,
            )
            items = _parse_excel(payload)
        except ScrapeError as e:
            failures.append(f"{label} {base}: {e}")
            errors.append(e)
            logger.warning("bizinfo %s failed via %s: %s", label, base, e)
        else:
            logger.info("bizinfo: %d items collected through official Excel", len(items))
            return BizinfoListResult(items=items, complete=True, channel="excel")
    return None


def _fetch_html_listings(
    client: HttpClient,
    *,
    timeout: float = LIST_PAGE_TIMEOUT,
) -> BizinfoListResult:
    """Walk every HTML page until empty and return merged items.

    A page that fails after retries stops pagination early rather than
    discarding everything gathered so far — but the result is marked
    `complete=False` so the caller knows NOT to trust it as "today's full
    list" (a partial list would otherwise make the diff classifier think
    every un-scraped item disappeared today — the same false-🔚종료 bug a
    total failure causes, just triggered by a partial fetch instead).
    Raises ScrapeError only when literally nothing was collected."""
    out: list[BizinfoRaw] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        try:
            html = _get_html_page(client, page, timeout=timeout)
        except ScrapeError as e:
            logger.warning(
                "bizinfo page %d failed, stopping pagination early (%d items "
                "already collected this run): %s", page, len(out), e,
            )
            if not out:
                raise
            return BizinfoListResult(items=out, complete=False)

        rows = _parse_list_page(html)
        if not rows:
            logger.debug("bizinfo page %d empty — stopping", page)
            break
        # Defensive: if every id was already seen we're in a pagination loop
        new_rows = [r for r in rows if r.pblanc_id not in seen]
        if not new_rows:
            logger.warning("bizinfo page %d returned only seen ids — stopping", page)
            break
        for r in new_rows:
            seen.add(r.pblanc_id)
        out.extend(new_rows)
    else:
        logger.warning("bizinfo hit MAX_PAGES=%d", MAX_PAGES)

    if not out:
        raise ScrapeError("bizinfo: 0 items across all pages")
    return BizinfoListResult(items=out, complete=True, channel="html")


def _get_html_page(
    client: HttpClient,
    page: int,
    *,
    timeout: float = LIST_PAGE_TIMEOUT,
) -> str:
    failures: list[str] = []
    errors: list[ScrapeError] = []
    params = {"cpage": page, "pageIndex": 1, "rows": 15, "schEndAt": "N"}
    for host_index, base in enumerate(BASE_MIRRORS):
        url = f"{base}{LIST_PATH}"
        try:
            return client.get(
                url,
                params=params,
                timeout=timeout,
                attempts=None if host_index == 0 else BACKUP_HOST_ATTEMPTS,
            )
        except ScrapeError as e:
            failures.append(f"{base}: {e}")
            errors.append(e)
    raise ScrapeError(
        f"bizinfo HTML page {page} failed on every host: " + " | ".join(failures),
        kind=errors[-1].kind if errors else None,
        transient=bool(errors) and all(error.transient for error in errors),
    )


def _is_origin_unreachable(error: ScrapeError) -> bool:
    """Return whether retrying another endpoint on the same origin is wasteful.

    The string fallback keeps compatibility with lightweight fake clients and
    with any persisted/forwarded errors produced before ``ScrapeError`` gained
    structured metadata.
    """
    if error.kind in _ORIGIN_UNREACHABLE_KINDS:
        return True
    message = str(error)
    return any(kind in message for kind in _ORIGIN_UNREACHABLE_KINDS)


def _list_page_timeout() -> float:
    raw = os.environ.get("BIZINFO_LIST_PAGE_TIMEOUT", "").strip()
    if not raw:
        return LIST_PAGE_TIMEOUT
    try:
        value = float(raw)
    except ValueError as e:
        raise ScrapeError(f"invalid BIZINFO_LIST_PAGE_TIMEOUT: {raw!r}") from e
    if not 1.0 <= value <= MAX_LIST_PAGE_TIMEOUT:
        raise ScrapeError(
            "BIZINFO_LIST_PAGE_TIMEOUT must be between "
            f"1 and {MAX_LIST_PAGE_TIMEOUT:.0f} seconds",
        )
    return value


def _fetch_official_api(client: HttpClient, api_key: str) -> list[BizinfoRaw]:
    text = client.get(
        f"{BASE}{API_PATH}",
        params={"crtfcKey": api_key, "dataType": "json", "searchCnt": 0},
    )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise ScrapeError(f"bizinfo API returned invalid JSON: {e}") from e

    if not isinstance(payload, dict):
        raise ScrapeError("bizinfo API returned a non-object payload")
    if payload.get("reqErr"):
        raise ScrapeError(f"bizinfo API rejected the request: {payload['reqErr']}")

    body = payload.get("jsonArray", payload)
    # The current API (confirmed 2026-08-10) returns ``jsonArray`` directly as
    # a list. Older samples wrapped that list in ``{"item": [...]}``, so keep
    # both official response shapes compatible.
    if isinstance(body, list):
        records = body
    elif isinstance(body, dict):
        records = body.get("item", [])
    else:
        raise ScrapeError("bizinfo API response has no usable jsonArray payload")
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list) or not records:
        raise ScrapeError("bizinfo API returned 0 items")

    out: list[BizinfoRaw] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ScrapeError(f"bizinfo API item {index} is not an object")
        pblanc_id = _clean(record.get("pblancId") or record.get("seq"))
        title = _clean(record.get("pblancNm") or record.get("title"))
        if not pblanc_id or not _PBLANC_RE.fullmatch(f"pblancId={pblanc_id}") or not title:
            raise ScrapeError(f"bizinfo API item {index} has no valid id/title")
        period = _normalize_period(_clean(record.get("reqstDt") or record.get("reqstBeginEndDe")))
        summary_html = _clean(record.get("bsnsSumryCn") or record.get("description"))
        summary = BeautifulSoup(summary_html, "lxml").get_text(" ", strip=True) if summary_html else None
        hashtags = tuple(
            tag.strip().lstrip("#")
            for tag in _clean(record.get("hashtags") or record.get("hashTags")).split(",")
            if tag.strip()
        )
        out.append(BizinfoRaw(
            pblanc_id=pblanc_id,
            title=title,
            apply_period=period,
            deadline=_parse_end_date(period) if period else None,
            organizer=_clean(record.get("jrsdInsttNm") or record.get("author")) or None,
            executor=_clean(record.get("excInsttNm")) or None,
            support_field=_clean(
                record.get("pldirSportRealmLclasCodeNm") or record.get("lcategory"),
            ) or None,
            summary=summary,
            target=_clean(record.get("trgetNm")) or None,
            hashtags=hashtags,
        ))

    total_raw = records[0].get("totCnt") if isinstance(records[0], dict) else None
    if total_raw:
        try:
            total = int(str(total_raw).replace(",", ""))
        except ValueError as e:
            raise ScrapeError(f"bizinfo API has invalid total count: {total_raw}") from e
        if total != len(out):
            raise ScrapeError(f"bizinfo API incomplete: expected {total}, received {len(out)}")
    _validate_unique_ids(out, "API")
    return out


def _merge_api_enrichment(
    excel_items: list[BizinfoRaw],
    api_items: list[BizinfoRaw],
) -> list[BizinfoRaw]:
    """Use Excel's dates/list fields and add richer API-only content."""
    api_by_id = {item.pblanc_id: item for item in api_items}
    excel_ids = {item.pblanc_id for item in excel_items}
    api_ids = set(api_by_id)
    if excel_ids != api_ids:
        logger.warning(
            "bizinfo API/Excel ids differ (api_only=%d, excel_only=%d)",
            len(api_ids - excel_ids),
            len(excel_ids - api_ids),
        )

    out: list[BizinfoRaw] = []
    for excel_item in excel_items:
        api_item = api_by_id.get(excel_item.pblanc_id)
        if api_item is None:
            out.append(excel_item)
            continue
        out.append(BizinfoRaw(
            pblanc_id=excel_item.pblanc_id,
            title=excel_item.title,
            apply_period=excel_item.apply_period,
            deadline=excel_item.deadline,
            organizer=excel_item.organizer,
            executor=excel_item.executor,
            support_field=excel_item.support_field,
            summary=api_item.summary,
            target=api_item.target,
            hashtags=api_item.hashtags,
        ))
    return out


def _parse_excel(payload: bytes) -> list[BizinfoRaw]:
    """Parse the official OOXML download without a heavyweight XLSX dependency."""
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as workbook:
            shared = _read_shared_strings(workbook)
            root = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise ScrapeError(f"bizinfo Excel is not a valid workbook: {e}") from e

    rows = root.findall(".//m:sheetData/m:row", _XLSX_NS)
    if len(rows) < 2:
        raise ScrapeError("bizinfo Excel contains no data rows")

    header = _excel_row(rows[0], shared)
    if not _EXCEL_HEADERS.issubset(set(header.values())):
        missing = sorted(_EXCEL_HEADERS - set(header.values()))
        raise ScrapeError(f"bizinfo Excel missing columns: {', '.join(missing)}")
    columns = {value: column for column, value in header.items()}

    out: list[BizinfoRaw] = []
    for row_number, row in enumerate(rows[1:], start=2):
        values = _excel_row(row, shared)
        record = {name: values.get(column, "").strip() for name, column in columns.items()}
        if not any(record.values()):
            continue
        url = record.get("공고상세URL", "")
        match = _PBLANC_RE.search(url)
        title = record.get("공고명", "")
        if not match or not title:
            raise ScrapeError(f"bizinfo Excel row {row_number} has no valid id/title")
        period = _join_period(record.get("신청시작일자"), record.get("신청종료일자"))
        out.append(BizinfoRaw(
            pblanc_id=match.group(1),
            title=title,
            apply_period=period,
            deadline=_parse_end_date(period) if period else None,
            organizer=record.get("소관부처") or None,
            executor=record.get("사업수행기관") or None,
            support_field=record.get("지원분야") or None,
        ))

    if len(out) != len(rows) - 1:
        raise ScrapeError(
            f"bizinfo Excel incomplete: {len(rows) - 1} rows but {len(out)} parsed",
        )
    _validate_unique_ids(out, "Excel")
    return out


def _read_shared_strings(workbook: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    except ET.ParseError as e:
        raise ScrapeError(f"bizinfo Excel shared strings are invalid: {e}") from e
    return ["".join(t.text or "" for t in item.findall(".//m:t", _XLSX_NS))
            for item in root.findall("m:si", _XLSX_NS)]


def _excel_row(row: ET.Element, shared: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for cell in row.findall("m:c", _XLSX_NS):
        reference = cell.get("r", "")
        column = re.sub(r"\d", "", reference)
        if not column:
            continue
        if cell.get("t") == "inlineStr":
            value = "".join(t.text or "" for t in cell.findall(".//m:t", _XLSX_NS))
        else:
            node = cell.find("m:v", _XLSX_NS)
            value = node.text or "" if node is not None else ""
            if cell.get("t") == "s" and value:
                try:
                    value = shared[int(value)]
                except (ValueError, IndexError) as e:
                    raise ScrapeError(f"bizinfo Excel has invalid shared-string index: {value}") from e
        values[column] = value
    return values


def _validate_unique_ids(items: list[BizinfoRaw], channel: str) -> None:
    ids = [item.pblanc_id for item in items]
    if not ids:
        raise ScrapeError(f"bizinfo {channel} returned 0 items")
    if len(ids) != len(set(ids)):
        raise ScrapeError(f"bizinfo {channel} returned duplicate announcement ids")


def _clean(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _normalize_period(period: str) -> str | None:
    if not period:
        return None
    dates = _DATE_RE.findall(period)
    normalized = [f"{year}-{month}-{day}" for year, month, day in dates]
    if len(normalized) >= 2:
        return f"{normalized[0]} ~ {normalized[-1]}"
    if normalized:
        return normalized[0]
    return period


def _join_period(start: str | None, end: str | None) -> str | None:
    start_value = _normalize_period(start or "")
    end_value = _normalize_period(end or "")
    if start_value and end_value:
        return f"{start_value} ~ {end_value}"
    return start_value or end_value


def _parse_list_page(html: str) -> list[BizinfoRaw]:
    soup = BeautifulSoup(html, "lxml")
    container = soup.find("div", class_="table_Type_1")
    if not isinstance(container, Tag):
        return []
    table = container.find("table")
    if not isinstance(table, Tag):
        return []
    tbody = table.find("tbody")
    if not isinstance(tbody, Tag):
        return []

    out: list[BizinfoRaw] = []
    for tr in tbody.find_all("tr", recursive=False):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 6:
            continue
        anchor = cells[2].find("a")
        if not isinstance(anchor, Tag):
            continue
        href = anchor.get("href", "")
        m = _PBLANC_RE.search(href if isinstance(href, str) else "")
        if not m:
            continue
        pblanc_id = m.group(1)
        title = anchor.get_text(strip=True)
        if not title:
            continue

        apply_period_raw = cells[3].get_text(" ", strip=True)
        apply_period = re.sub(r"\s+", " ", apply_period_raw).strip() or None
        deadline = _parse_end_date(apply_period) if apply_period else None

        support_field = cells[1].get_text(strip=True) or None
        organizer = cells[4].get_text(strip=True) or None
        executor = cells[5].get_text(strip=True) or None

        out.append(BizinfoRaw(
            pblanc_id=pblanc_id,
            title=title,
            apply_period=apply_period,
            deadline=deadline,
            organizer=organizer,
            executor=executor,
            support_field=support_field,
        ))
    return out


def _parse_end_date(period: str) -> date | None:
    """Pull the END date out of a '2026-05-15 ~ 2026-06-12' style range. If
    only one date is present, treat it as the deadline. Returns None on
    unparseable input."""
    dates = _DATE_RE.findall(period)
    if not dates:
        return None
    y, m, d = dates[-1]
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def detail_url(pblanc_id: str) -> str:
    """Canonical detail URL — matches the legacy archive's stored URLs."""
    return f"{BASE}{DETAIL_PATH}?pblancId={pblanc_id}"


@dataclass(frozen=True)
class BizinfoDetail:
    """Fields extracted from the bizinfo DETAIL page (beyond what the list
    page provides). All optional — empty strings/None when the page omits a
    section. Used by the adapter to enrich `Item` (set badges, summary)."""
    pblanc_id: str
    summary: str | None        # 사업개요 본문 (truncated)
    hashtags: tuple[str, ...]  # 해시태그 (#인공지능, #창업, …)
    region: str | None         # 지역 (해당 시도/지역)
    target: str | None         # 지원대상
    amount: str | None         # 지원금액


_HASHTAG_RE = re.compile(r"#[^\s<>#]{1,30}")
_OVERVIEW_HEADINGS = ("사업개요", "사업 개요", "개요")


def fetch_detail(client: HttpClient, pblanc_id: str) -> BizinfoDetail:
    """Fetch one DETAIL page and extract enrichment fields. Best-effort —
    missing sections silently return None/empty. Used for the FIRST sighting
    of a stable_id only (subsequent runs hit the DB cache via COALESCE)."""
    html = client.get(detail_url(pblanc_id))
    soup = BeautifulSoup(html, "lxml")

    # 1. 사업개요 — collect <p>/<div> text under any element whose text starts
    # with one of the overview headings. Fallback: the longest <div class="txt">.
    summary: str | None = None
    for el in soup.find_all(["dt", "dl", "th", "h3", "h4", "strong"]):
        if not isinstance(el, Tag):
            continue
        head_text = el.get_text(strip=True)
        if any(head_text.startswith(h) for h in _OVERVIEW_HEADINGS):
            # Search the next siblings / nearest <dd>/<div> for the body text
            sibling = el.find_next(["dd", "div", "p"])
            if isinstance(sibling, Tag):
                body = sibling.get_text(" ", strip=True)
                if body:
                    summary = body[:500]
                    break
    if not summary:
        # Fallback: longest div.txt with substantial Korean text
        candidates = [
            d for d in soup.find_all("div", class_="txt")
            if isinstance(d, Tag) and len(d.get_text(strip=True)) > 30
        ]
        if candidates:
            summary = max(
                (c.get_text(" ", strip=True) for c in candidates),
                key=len,
            )[:500]

    # 2. Hashtags — bizinfo detail pages render hashtags like `<a>#인공지능</a>`.
    # Search the entire body text (the list also catches anything in the
    # 'related categories' / 'tags' panel without being too greedy).
    page_text = soup.get_text(" ", strip=True)
    hashtags = tuple(sorted(set(_HASHTAG_RE.findall(page_text))))

    # 3. Optional 지역 / 지원대상 / 지원금액 — many detail pages use a label/value
    # <table>; pick them up best-effort.
    region: str | None = None
    target: str | None = None
    amount: str | None = None
    for row in soup.find_all("tr"):
        if not isinstance(row, Tag):
            continue
        cells = row.find_all(["th", "td"], limit=2)
        if len(cells) != 2:
            continue
        label = cells[0].get_text(strip=True)
        value = cells[1].get_text(" ", strip=True)
        if not value:
            continue
        if "지역" in label and region is None:
            region = value[:100]
        elif "지원대상" in label and target is None:
            target = value[:200]
        elif "지원금액" in label and amount is None:
            amount = value[:200]

    return BizinfoDetail(
        pblanc_id=pblanc_id,
        summary=summary,
        hashtags=hashtags,
        region=region,
        target=target,
        amount=amount,
    )
