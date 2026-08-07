"""Day-1 / re-seed bootstrap. Fetch each archive repo's most recent
YYYY-MM-DD.html via `gh api`, parse the item cards out, and write a seed
snapshot to state/<archive>.json.

This parser is best-effort: malformed or unusually-styled item cards are
skipped with a warning instead of failing the seed run. It is intentionally
separate from production rendering code — if the renderer's output format
changes later, this parser's input (the *existing* historical HTML) does
not change, so the parser does not need to evolve."""

from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import warnings
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from .. import db
from ..config.archives import ARCHIVE_ORDER, ARCHIVES, ArchiveConfig
from ..config.categories import Category
from ..config.sources import SOURCES, SourceDef
from ..models import Item

# Reverse map: source display label → source_key. Multiple K-Startup sources
# share the same "K-Startup 사업소개" label, so we resolve those by archive
# (the seed parser knows which archive it's working on).
_DISPLAY_TO_KEY: dict[str, str] = {}
for s in SOURCES.values():
    # The first source with a given display wins; archive-specific overrides
    # are handled per-archive in _pick_source_key below.
    _DISPLAY_TO_KEY.setdefault(s.display, s.key)


@dataclass
class SeedResult:
    archive_key: str
    snapshot_date: date
    items: list[Item]
    skipped_cards: int
    warnings: list[str]


def _gh_api(path: str) -> bytes:
    """Run `gh api` and return raw bytes. Raises CalledProcessError on failure."""
    return subprocess.run(
        ["gh", "api", path],
        check=True, capture_output=True,
    ).stdout


def _list_archive_dates(repo: str) -> list[date]:
    raw = _gh_api(f"repos/{repo}/contents")
    files = json.loads(raw)
    dates: list[date] = []
    for f in files:
        name = f.get("name", "")
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.html", name)
        if m:
            try:
                dates.append(date.fromisoformat(m.group(1)))
            except ValueError:
                pass
    dates.sort()
    return dates


def _fetch_html(repo: str, snapshot_date: date) -> str:
    raw = _gh_api(f"repos/{repo}/contents/{snapshot_date.isoformat()}.html")
    payload = json.loads(raw)
    content_b64 = payload["content"]
    return base64.b64decode(content_b64).decode("utf-8")


def _pick_source_key(cfg: ArchiveConfig, display_label: str) -> str | None:
    """Resolve the source-tag display label to a source_key. For archives whose
    sources tuple has a single source_key, always use that — short-circuits the
    K-Startup ambiguity (3 archives share '사업소개' label)."""
    if len(cfg.sources) == 1:
        return cfg.sources[0]
    key = _DISPLAY_TO_KEY.get(display_label)
    if key in cfg.sources:
        return key
    return None


def _stable_id(source_key: str, detail_url: str) -> str:
    src: SourceDef | None = SOURCES.get(source_key)
    sid: str | None = None
    if src is not None:
        sid = src.id_rule(detail_url)
    if sid:
        return f"{source_key}:{sid}"
    # Fallback: normalized URL hash. Carry the source_key prefix so collisions
    # between archives don't merge.
    import hashlib
    p = urlparse(detail_url)
    normalized = f"{p.scheme}://{p.netloc}{p.path}?{p.query}"
    return f"{source_key}:url:{hashlib.sha1(normalized.encode()).hexdigest()[:16]}"


_DEADLINE_RE = re.compile(r"D-\d+")
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _parse_apply_period(raw: str) -> tuple[str, date | None]:
    """Strip any D-N badge prefix from the rendered apply_period text and try
    to extract the end-date for `Item.deadline`."""
    cleaned = _DEADLINE_RE.sub("", raw).replace("🔥", "").replace("⚠️", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    dates = _DATE_RE.findall(cleaned)
    deadline: date | None = None
    if len(dates) >= 2:
        try:
            deadline = date(int(dates[-1][0]), int(dates[-1][1]), int(dates[-1][2]))
        except ValueError:
            deadline = None
    return cleaned, deadline


def _row_value(td: Tag) -> str:
    """Get text from a metadata <td>, stripping the D-N urgency badge spans."""
    text = td.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _parse_card(
    card: Tag, cfg: ArchiveConfig, current_category: Category | None,
) -> Item | None:
    """Extract one Item from a div.item-card. Returns None if essential fields
    (title or detail URL) can't be found — caller logs and counts."""
    # 1. Title + detail URL: the first <a> inside the card's first child div.
    title_div = card.find("div", recursive=False)
    if title_div is None:
        return None
    anchor = title_div.find("a") if isinstance(title_div, Tag) else None
    if anchor is None or not anchor.get("href"):
        return None
    title = anchor.get_text(strip=True)
    detail_url = anchor["href"].strip()
    if not title or not detail_url:
        return None

    # 2. Optional 🖥️ pill badges next to the title (these have a colored bg).
    badges: list[str] = []
    if isinstance(title_div, Tag):
        for span in title_div.find_all("span"):
            txt = span.get_text(strip=True)
            # Heuristic: badges typically begin with an emoji and are short.
            if txt and not txt.startswith("📌"):
                badges.append(txt)

    # 3. Source-tag pill: a <span> with background:#e8eaed.
    source_label = None
    for span in card.find_all("span"):
        style = (span.get("style") or "")
        if "#e8eaed" in style:
            source_label = span.get_text(strip=True)
            break
    source_key = _pick_source_key(cfg, source_label or "") if source_label else None
    if not source_key:
        # No usable source tag — skip rather than fabricate, so we don't
        # poison the diff with phantom items.
        return None

    # 4. Metadata table — iterate rows; first td is the label, second the value.
    organizer = amount = apply_period = region = target = None
    deadline: date | None = None
    table = card.find("table")
    if isinstance(table, Tag):
        for tr in table.find_all("tr"):
            tds = tr.find_all("td", limit=2)
            if len(tds) != 2:
                continue
            label = tds[0].get_text(strip=True)
            value = _row_value(tds[1])
            if "주관기관" in label:
                organizer = value
            elif "지원금액" in label:
                amount = value
            elif "신청기간" in label:
                apply_period, deadline = _parse_apply_period(value)
            elif "지역" in label:
                region = value
            elif "지원대상" in label:
                target = value

    # 5. Optional <p> summary at the end.
    summary = None
    p_tag = card.find("p")
    if isinstance(p_tag, Tag):
        summary_text = p_tag.get_text(separator=" ", strip=True)
        if summary_text:
            summary = summary_text

    category_key: str | None = None
    if cfg.categories and current_category is not None:
        category_key = current_category.key

    return Item(
        stable_id=_stable_id(source_key, detail_url),
        source_key=source_key,
        title=title,
        detail_url=detail_url,
        category=category_key,
        organizer=organizer,
        amount=amount,
        apply_period=apply_period,
        deadline=deadline,
        region=region,
        target=target,
        summary=summary,
        badges=tuple(badges),
    )


def _match_category(h3_text: str, cats: tuple[Category, ...]) -> Category | None:
    """Match an <h3> like '🔬 R&D (9건)' to a known category by Korean name."""
    cleaned = re.sub(r"\(\d+건\)", "", h3_text).strip()
    for c in cats:
        # Compare ignoring spaces/dots/dashes — handles '시설/공간' vs '시설·공간'.
        norm = lambda s: re.sub(r"[\s·/\-]", "", s)
        if norm(c.name) in norm(cleaned):
            return c
    return None


def parse_archive_html(cfg: ArchiveConfig, html: str) -> tuple[list[Item], int, list[str]]:
    """Return (items, skipped_count, warnings)."""
    soup = BeautifulSoup(html, "lxml")
    items: list[Item] = []
    skipped = 0
    warns: list[str] = []

    # Item cards are divs whose inline style contains 'border-left:4px solid'.
    # We walk the document tree in document order so we can track the current
    # category header (the most recent <h3> with a known category name).
    current_category: Category | None = None
    for el in soup.find_all(["h3", "div"]):
        if not isinstance(el, Tag):
            continue
        if el.name == "h3" and cfg.categories:
            cat = _match_category(el.get_text(" ", strip=True), cfg.categories)
            if cat is not None:
                current_category = cat
            continue
        style = el.get("style") or ""
        if "border-left:4px solid" not in style:
            continue
        try:
            item = _parse_card(el, cfg, current_category)
        except Exception as e:  # noqa: BLE001
            skipped += 1
            warns.append(f"card parse error: {e!r}")
            continue
        if item is None:
            skipped += 1
            continue
        items.append(item)

    # Deduplicate by stable_id (some archives list the same item under multiple
    # categories — first occurrence wins).
    seen: set[str] = set()
    unique: list[Item] = []
    for i in items:
        if i.stable_id in seen:
            continue
        seen.add(i.stable_id)
        unique.append(i)

    return unique, skipped, warns


def seed_archive(client, cfg: ArchiveConfig) -> SeedResult:
    """Parse the archive's most recent HTML into Items and insert into the DB
    (`item` upsert + `daily_status` rows with status='ongoing'). `client` is a
    live libsql client; the caller is responsible for opening + closing it."""
    dates = _list_archive_dates(cfg.repo)
    if not dates:
        warnings.warn(f"{cfg.key}: no YYYY-MM-DD.html in repo {cfg.repo}")
        return SeedResult(
            cfg.key, datetime.now(UTC).date(), [], 0, [f"empty repo {cfg.repo}"]
        )
    snap = dates[-1]
    html = _fetch_html(cfg.repo, snap)
    items, skipped, warns = parse_archive_html(cfg, html)
    db.insert_seed_snapshot(client, cfg.key, snap, items)
    return SeedResult(cfg.key, snap, items, skipped, warns)


def seed_all() -> dict[str, SeedResult]:
    """Open one DB client, migrate schema, seed each archive in turn. Returns
    a per-archive result map (caller prints / logs it)."""
    out: dict[str, SeedResult] = {}
    client = db.connect()
    try:
        db.migrate(client)
        for k in ARCHIVE_ORDER:
            try:
                out[k] = seed_archive(client, ARCHIVES[k])
                print(
                    f"seed[{k}]: parsed {len(out[k].items)} items "
                    f"from {out[k].snapshot_date} (skipped {out[k].skipped_cards} cards)",
                    file=sys.stderr,
                )
            except subprocess.CalledProcessError as e:
                print(f"seed[{k}]: gh api failed: {e}", file=sys.stderr)
                out[k] = SeedResult(
                    k, datetime.now(UTC).date(), [], 0, [f"gh api failed: {e}"]
                )
    finally:
        client.close()
    return out
