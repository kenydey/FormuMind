"""PDF table extraction contract — structured table assets, chemistry-first.

W2-3 / P1-18. Parsers (Docling / MinerU) already emit tables as Markdown/HTML
by-products; this module turns them into *structured assets* with provenance:

* :class:`TableAsset` — one table: id, source, page, caption, kind, headers,
  rows, raw markdown/HTML, parser provenance.
* ``kind`` — chemistry-first heuristic: ``recipe`` （配方表） /
  ``performance`` （性能对比表） / ``tds_sds`` (TDS/SDS 表格） / ``other``.
  Confidence < 0.5 degrades to ``other`` instead of guessing.
* :func:`extract_tables` — normalise parsed blocks into assets. Blocks are
  duck-typed (``type`` / ``page_idx`` / ``text`` / ``html`` / ``caption``),
  so MinerU native blocks (``MinerUBlock``) pass straight in; use
  :func:`blocks_from_markdown` for markdown-only parser output.
* Sidecar persistence — :func:`save_tables` / :func:`load_tables` keep assets
  as JSON next to SourceDocument metadata (``data/source_tables/<key>.json``);
  the DB schema is untouched. P2: the key is now the SourceDocument UUID
  (re-keyed at persist time by ``parsing.persist_table_sidecar`` / F-3) —
  the old content-sha256 key is legacy: sidecars written under the sha256 key
  are orphans no reader will open. See ``scripts/`` for cleanup tooling.
  W3-1: ``save_tables`` accepts optional ``property_sets``
  (normalised :class:`table_normalize.PropertySet` dicts) persisted under the
  ``"property_sets"`` key of the same sidecar; :func:`load_property_sets`
  reads them back.
"""
from __future__ import annotations

import html as _html
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# ── data model ──────────────────────────────────────────────────────────────


@dataclass
class TableAsset:
    """One structured table extracted from a parsed document."""

    table_id: str  # "<source_id>#p<page:02d>-<idx:02d>"
    source_id: str  # SourceDocument.id (re-keyed at persist time)
    page_no: int  # 1-based
    caption: str
    provenance: dict = field(default_factory=dict)  # parser/parser_version/extracted_at
    kind: str = "other"  # recipe | performance | tds_sds | other
    headers: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    raw_markdown: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TableAsset":
        return cls(
            table_id=str(data.get("table_id", "")),
            source_id=str(data.get("source_id", "")),
            page_no=int(data.get("page_no", 1) or 1),
            caption=str(data.get("caption", "")),
            provenance=dict(data.get("provenance") or {}),
            kind=str(data.get("kind", "other")),
            headers=list(data.get("headers") or []),
            rows=list(data.get("rows") or []),
            raw_markdown=str(data.get("raw_markdown", "")),
        )


@dataclass
class MdBlock:
    """Normalised input block (markdown-derived; mirrors MinerUBlock fields)."""

    type: str = "text"  # "table" | "text"
    page_idx: int = 0  # 0-based
    text: str = ""
    html: str = ""
    caption: str = ""


# ── chemistry-first classification ──────────────────────────────────────────

# Priority order on ties: tds_sds (document-type signal) > recipe > performance.
_KIND_PRIORITY = ("tds_sds", "recipe", "performance")

_KIND_KEYWORDS: dict[str, tuple[str, ...]] = {
    "tds_sds": (
        "tds", "sds", "msds", "技术数据表", "技术数据", "安全数据表",
        "安全技术说明书",
    ),
    "recipe": (
        "成分", "配比", "质量份", "份数", "phr", "配方", "原料", "组分",
        "添加量", "用量", "组成", "含量", "wt%", "wt％",
    ),
    "performance": (
        "性能", "测试", "结果", "标准", "指标", "拉伸", "硬度", "附着力",
        "试验", "对比", "测试方法",
    ),
}

_LATIN_WORD_RES: dict[str, re.Pattern] = {}


def _keyword_hits(text: str) -> dict[str, int]:
    """Count keyword hits per kind. Latin keywords match whole words.（大小写不敏感）"""
    lowered = text.lower()
    hits: dict[str, int] = {}
    for kind, keywords in _KIND_KEYWORDS.items():
        n = 0
        for kw in keywords:
            if kw.isascii() and kw.isalpha():
                pat = _LATIN_WORD_RES.get(kw)
                if pat is None:
                    pat = _LATIN_WORD_RES[kw] = re.compile(
                        r"\b" + re.escape(kw) + r"\b", re.IGNORECASE
                    )
                if pat.search(text):
                    n += 1
            elif kw in lowered:
                n += 1
        hits[kind] = n
    return hits


def _confidence(hits: int) -> float:
    if hits <= 0:
        return 0.0
    if hits == 1:
        return 0.4
    if hits == 2:
        return 0.65
    return 0.9


def classify_table(headers: Iterable[str], caption: str = "") -> tuple[str, float]:
    """Chemistry-first kind classification.

    Caption hits count double (authorial intent). Returns ``(kind, confidence)``;
    confidence < 0.5 degrades to ``"other"`` rather than guessing.
    """
    header_text = " | ".join(str(h) for h in headers)
    caption_hits = _keyword_hits(caption or "")
    header_hits = _keyword_hits(header_text)
    total = {
        kind: 2 * caption_hits[kind] + header_hits[kind] for kind in _KIND_PRIORITY
    }
    best = max(_KIND_PRIORITY, key=lambda k: (total[k], -_KIND_PRIORITY.index(k)))
    conf = _confidence(total[best])
    if conf < 0.5:
        return "other", conf
    return best, conf


# ── markdown → blocks ───────────────────────────────────────────────────────

_PAGE_MARKER_RE = re.compile(r"<!--\s*page:\s*(\d+)\s*-->")
_DOCLING_BREAK = "<!-- docling-page-break -->"
_CAPTION_RES = (
    re.compile(r"表\s*\d+"),
    re.compile(r"Table\s*\d+", re.IGNORECASE),
)
_HTML_TABLE_RE = re.compile(r"<table\b.*?</table\s*>", re.IGNORECASE | re.DOTALL)
_SEPARATOR_CELL_RE = re.compile(r"^:?-+:?$")


def _clean_caption(line: str) -> str:
    return line.replace("**", "").replace("__", "").strip(" :：\t")


def _is_caption_line(line: str) -> bool:
    stripped = _clean_caption(line)
    return any(p.search(stripped) for p in _CAPTION_RES)


def _is_separator_line(line: str) -> bool:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    non_empty = [c for c in cells if c]
    return bool(non_empty) and all(_SEPARATOR_CELL_RE.match(c) for c in non_empty)


def _looks_like_pipe_table(text: str) -> bool:
    lines = [ln for ln in text.splitlines() if ln.strip().startswith("|")]
    return len(lines) >= 2 and any(_is_separator_line(ln) for ln in lines)


def _split_pipe_row(line: str) -> list[str]:
    # Split on unescaped pipes.
    cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [c.replace("\\|", "|").strip() for c in cells]


def _parse_pipe_table(text: str) -> tuple[list[str], list[list[str]]]:
    """Pipe-table markdown → (headers, rows). Separator row is skipped."""
    lines = [ln for ln in text.splitlines() if ln.strip().startswith("|")]
    parsed = [_split_pipe_row(ln) for ln in lines]
    headers: list[str] = []
    rows: list[list[str]] = []
    for cells in parsed:
        if _is_separator_line("|" + "|".join(cells) + "|"):
            continue  # separator row
        if not headers:
            headers = cells
        else:
            rows.append(cells)
    return headers, rows


class _TableHTMLParser(HTMLParser):
    """Minimal <table> → rows of cell text (stdlib only)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            text = _html.unescape("".join(self._cell)).strip()
            self._row.append(re.sub(r"\s+", " ", text))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(c.strip() for c in self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _parse_html_table(html_text: str) -> tuple[list[str], list[list[str]]]:
    parser = _TableHTMLParser()
    try:
        parser.feed(html_text)
    except Exception:
        logger.exception("table_contract: HTML table parse failed")
        return [], []
    rows = parser.rows
    if not rows:
        return [], []
    return rows[0], rows[1:]


def blocks_from_markdown(markdown: str) -> list[MdBlock]:
    """Split parser markdown into blocks; pipe/HTML tables become table blocks.

    Page tracking: ``<!-- page:N -->`` markers (what chunking consumes) and the
    raw docling ``<!-- docling-page-break -->`` placeholder. Captions are
    matched nearby: up to 3 text lines before the table, else 1 line after.
    """
    lines = (markdown or "").splitlines()
    blocks: list[MdBlock] = []
    # line number where each table block starts (for caption matching).
    table_line_nos: list[int] = []
    page_idx = 0  # 0-based

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = _PAGE_MARKER_RE.search(line)
        if m:
            page_idx = max(int(m.group(1)) - 1, 0)
            i += 1
            continue
        if _DOCLING_BREAK in line:
            page_idx += 1
            i += 1
            continue

        stripped = line.strip()
        # HTML table (may span lines).
        if "<table" in stripped.lower():
            buf = [line]
            j = i + 1
            while j < n and "</table" not in buf[-1].lower():
                buf.append(lines[j])
                j += 1
            blocks.append(MdBlock(type="table", page_idx=page_idx,
                                 html="\n".join(buf), text=""))
            table_line_nos.append(i)
            i = j
            continue

        # Pipe table: run of '|' lines containing a separator row.
        if stripped.startswith("|"):
            buf = [line]
            j = i + 1
            while j < n and lines[j].strip().startswith("|"):
                buf.append(lines[j])
                j += 1
            candidate = "\n".join(buf)
            if _looks_like_pipe_table(candidate):
                blocks.append(MdBlock(type="table", page_idx=page_idx,
                                      text=candidate))
                table_line_nos.append(i)
                i = j
                continue
            # Not a real table — fall through and re-scan line by line.

        i += 1

    # Caption matching (nearby): up to 3 text lines before, else 1 line after.
    for block, start in zip(blocks, table_line_nos):
        for ln_no in range(start - 1, max(start - 4, -1), -1):
            if 0 <= ln_no < n and lines[ln_no].strip() \
                    and _is_caption_line(lines[ln_no]):
                block.caption = _clean_caption(lines[ln_no])
                break
        else:
            end_span = len((block.html or block.text).strip().splitlines())
            after = start + end_span
            if after < n and lines[after].strip() \
                    and _is_caption_line(lines[after]):
                block.caption = _clean_caption(lines[after])

    return blocks


# ── extraction ──────────────────────────────────────────────────────────────

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def extract_tables(
    source_id: str,
    parsed_blocks: Iterable[Any],
    *,
    parser: str = "unknown",
) -> list[TableAsset]:
    """Normalise parsed blocks into :class:`TableAsset` list.

    ``parsed_blocks`` are duck-typed: ``type`` / ``page_idx`` / ``text`` /
    ``html`` / ``caption`` (MinerU native blocks satisfy this directly).
    Never raises for bad blocks — unparseable input yields an asset with
    empty headers/rows and the raw text preserved.
    """
    assets: list[TableAsset] = []
    idx = 0
    for block in parsed_blocks or []:
        try:
            if (getattr(block, "type", "") or "") != "table":
                continue
            page_idx = getattr(block, "page_idx", 0) or 0
            page_no = int(page_idx) + 1
            caption = _clean_caption(str(getattr(block, "caption", "") or ""))
            html_text = str(getattr(block, "html", "") or "")
            text = str(getattr(block, "text", "") or "")

            headers: list[str] = []
            rows: list[list[str]] = []
            if "<table" in html_text.lower():
                headers, rows = _parse_html_table(html_text)
                raw = html_text.strip()
            elif _looks_like_pipe_table(text):
                headers, rows = _parse_pipe_table(text)
                raw = text.strip()
            else:
                raw = (html_text or text).strip()

            kind, conf = classify_table(headers, caption)
            prefix = f"{source_id}#p{page_no:02d}-{idx:02d}" if source_id else f"p{page_no:02d}-{idx:02d}"
            assets.append(
                TableAsset(
                    table_id=prefix,
                    source_id=source_id or "",
                    page_no=page_no,
                    caption=caption,
                    provenance={
                        "parser": parser,
                        "parser_version": "unknown",
                        "extracted_at": _utcnow_iso(),
                        "confidence": conf,
                    },
                    kind=kind,
                    headers=headers,
                    rows=rows,
                    raw_markdown=raw,
                )
            )
            idx += 1
        except Exception:
            logger.exception("table_contract: skipping unparseable table block")
    return assets


# ── sidecar persistence (no DB schema change) ─────────────────────────────────

def _tables_root() -> Path:
    override = os.environ.get("FORMUMIND_TABLES_DIR")
    base = Path(override) if override else Path("./data/source_tables")
    return base.resolve()


def _tables_path(key: str) -> Path:
    safe = re.sub(r"[^\w.\-]", "_", key)[:80] or "anon"
    return _tables_root() / f"{safe}.json"


def save_tables(
    source_id: str,
    tables: list[TableAsset],
    *,
    property_sets: list[dict] | None = None,
) -> Path | None:
    """Persist assets as JSON sidecar. Fail-open: returns None on any error.

    W3-1: ``property_sets`` (normalised ``PropertySet.to_dict()`` dicts) are
    stored under the ``"property_sets"`` key of the same sidecar — no DB
    schema change, old sidecars without the key keep loading fine.
    """
    if not source_id:
        return None
    try:
        path = _tables_path(source_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source_id": source_id,
            "extracted_at": _utcnow_iso(),
            "tables": [t.to_dict() for t in tables],
        }
        if property_sets is not None:
            payload["property_sets"] = property_sets
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except Exception:
        logger.exception("table_contract: save_tables failed (fail-open)")
        return None


def load_tables(source_id: str) -> list[TableAsset]:
    """Load assets for a source id (or content-hash key). Empty on any error."""
    try:
        path = _tables_path(source_id)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [TableAsset.from_dict(d) for d in payload.get("tables", [])]
    except Exception:
        logger.exception("table_contract: load_tables failed (fail-open)")
        return []


def load_property_sets(source_id: str) -> list[dict]:
    """Load normalised PropertySet dicts (W3-1) for a source id.

    Returns plain dicts (use ``table_normalize.PropertySet.from_dict`` to
    rehydrate); empty list when the sidecar is missing or has no
    ``"property_sets"`` key. Never raises.
    """
    try:
        path = _tables_path(source_id)
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        sets = payload.get("property_sets") or []
        return [dict(s) for s in sets if isinstance(s, dict)]
    except Exception:
        logger.exception("table_contract: load_property_sets failed (fail-open)")
        return []
