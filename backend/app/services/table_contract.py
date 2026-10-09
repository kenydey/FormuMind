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

from ._fsutil import atomic_write_text, read_text_with_retry

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


# Column-header vocabulary of a property table ("项目 | 指标 | 单位", "Property | Value | Unit"). Shared with
# ``table_normalize``, which uses the same words to find the name / value / unit columns — a table it can read
# column by column should not be refused at the door because its caption carries no topical keyword.
NAME_COL_HINTS = (
    "项目", "性能项目", "检验项目", "测试项目", "指标名称",
    "组分", "成分", "名称", "property", "item", "characteristic",
)
VALUE_COL_HINTS = (
    "典型值", "指标值", "指标", "测试结果", "结果", "实测值", "数值",
    "要求", "技术要求", "value", "typical", "result", "requirement",
    "specification",
)
UNIT_COL_HINTS = ("单位", "unit",)

# Confidence of a kind inferred from the column layout alone (below two keyword hits, above the 0.5 floor).
_SHAPE_CONFIDENCE = 0.6


def _has_property_table_shape(headers: Iterable[str]) -> bool:
    """A name column *and a different* value column — the layout of a datasheet / spec table."""
    keys = [str(h).lower() for h in headers]
    name_cols = {i for i, h in enumerate(keys) if any(hint in h for hint in NAME_COL_HINTS)}
    value_cols = {i for i, h in enumerate(keys) if any(hint in h for hint in VALUE_COL_HINTS)}
    return bool(name_cols) and bool(value_cols - name_cols)


def classify_table(headers: Iterable[str], caption: str = "") -> tuple[str, float]:
    """Chemistry-first kind classification.

    Caption hits count double (authorial intent). Returns ``(kind, confidence)``;
    confidence < 0.5 degrades to ``"other"`` rather than guessing — except that a table laid out as a
    property table (a name column next to a value column) is a ``performance`` table even when neither
    its caption nor its headers name a topic (a spreadsheet sheet, an English ``Property | Value | Unit``).
    """
    headers = [str(h) for h in headers]
    header_text = " | ".join(headers)
    caption_hits = _keyword_hits(caption or "")
    header_hits = _keyword_hits(header_text)
    total = {
        kind: 2 * caption_hits[kind] + header_hits[kind] for kind in _KIND_PRIORITY
    }
    best = max(_KIND_PRIORITY, key=lambda k: (total[k], -_KIND_PRIORITY.index(k)))
    conf = _confidence(total[best])
    if conf < 0.5:
        if _has_property_table_shape(headers):
            return "performance", _SHAPE_CONFIDENCE
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


_HEADING_MARK_RE = re.compile(r"^\s*#{1,6}\s+")


def _clean_caption(line: str) -> str:
    # A caption that the parser promoted to a heading arrives as "## 表1 典型性能".
    line = _HEADING_MARK_RE.sub("", line)
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


_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_WHOLE_EMPHASIS_RE = re.compile(r"^(\*\*|__)(.+?)\1$")


def _clean_cell(cell: str) -> str:
    """Cell text as a reader would see it: no ``<br>`` line breaks, no emphasis wrapped around the whole cell.

    Layout parsers bold header rows (``**Sample**``) and break wrapped cells with ``<br>``; left in, those
    markers end up in header names, so ``_header_hits`` / unit detection / classification compare against
    ``**单位**`` instead of ``单位``. The untouched text stays available as ``TableAsset.raw_markdown``.
    """
    cell = re.sub(r"\s+", " ", _BR_RE.sub(" ", cell)).strip()
    m = _WHOLE_EMPHASIS_RE.match(cell)
    return m.group(2).strip() if m else cell


def _split_pipe_row(line: str) -> list[str]:
    # Split on unescaped pipes.
    cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
    return [_clean_cell(c.replace("\\|", "|")) for c in cells]


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
    """Minimal <table> → rows of cell text (stdlib only).

    v29 Phase2 M-10: 支持 rowspan/colspan —— 此前忽略 span 属性，
    合并单元格导致列错位。
    v2 H-4: 列占用追踪 —— 非首列 rowspan 时新单元格跳过被占列；
    嵌套 table 深度追踪；未闭合 tr 自动 flush。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._cell_span: tuple[int, int] = (1, 1)  # (rowspan, colspan)
        # 跨行占位：{row_idx: {col_idx: text}}，用于 rowspan 填充
        self._rowspan_fill: dict[int, dict[int, str]] = {}
        self._cur_row_idx: int = 0
        # v2 H-4: 嵌套 table 深度（>0 时内层内容不污染外层）
        self._table_depth: int = 0
        # v2 H-4: 当前行被占用的列（rowspan 遗留 + 本行已写）
        self._occupied: set[int] = set()

    def _next_free_col(self) -> int:
        """v2 H-4: 找当前行下一个未被占用的列。"""
        col = 0
        while col in self._occupied:
            col += 1
        return col

    def handle_starttag(self, tag: str, attrs: list) -> None:
        tag = tag.lower()
        if tag == "table":
            self._table_depth += 1
            return
        if self._table_depth > 1:
            return  # 嵌套表内层：跳过，避免污染外层
        if tag == "tr":
            # v2 H-4: 未闭合 tr 自动 flush 上一行
            if self._row is not None:
                self._flush_row()
            self._row = []
            self._occupied = set()
            # 填充上一行 rowspan 留下的单元格
            fill = self._rowspan_fill.pop(self._cur_row_idx, {})
            for col in sorted(fill):
                self._occupied.add(col)
                # 确保 _row 长度足够
                while len(self._row) <= col:
                    self._row.append("")
                self._row[col] = fill[col]
        elif tag in ("td", "th"):
            self._cell = []
            # 解析 rowspan/colspan
            ad = {k.lower(): v for k, v in attrs}
            try:
                rs = max(1, int(ad.get("rowspan", 1)))
            except (ValueError, TypeError):
                rs = 1
            try:
                cs = max(1, int(ad.get("colspan", 1)))
            except (ValueError, TypeError):
                cs = 1
            self._cell_span = (rs, cs)
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def _flush_row(self) -> None:
        """v2 H-4: 刷新当前行（用于 tr 结束或新 tr 开始时的未闭合处理）。"""
        if self._row is not None and any(c.strip() for c in self._row):
            self.rows.append(self._row)
        self._row = None
        self._cur_row_idx += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "table":
            self._table_depth = max(0, self._table_depth - 1)
            return
        if self._table_depth > 1:
            return
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            text = _html.unescape("".join(self._cell)).strip()
            text = re.sub(r"\s+", " ", text)
            rs, cs = self._cell_span
            # v2 H-4: 找未被占用的列，而非 len(self._row)
            col_idx = self._next_free_col()
            # 确保 _row 长度足够
            while len(self._row) <= col_idx:
                self._row.append("")
            self._row[col_idx] = text
            self._occupied.add(col_idx)
            # colspan：横向占用
            for c in range(1, cs):
                self._occupied.add(col_idx + c)
                while len(self._row) <= col_idx + c:
                    self._row.append("")
                self._row[col_idx + c] = ""
            # rowspan：记录后续行需填充的位置
            if rs > 1:
                for r in range(1, rs):
                    fill_row = self._cur_row_idx + r
                    if fill_row not in self._rowspan_fill:
                        self._rowspan_fill[fill_row] = {}
                    for c in range(cs):
                        self._rowspan_fill[fill_row][col_idx + c] = text if c == 0 else ""
            self._cell = None
            self._cell_span = (1, 1)
        elif tag == "tr" and self._row is not None:
            self._flush_row()

    def handle_data(self, data: str) -> None:
        if self._cell is not None and self._table_depth <= 1:
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


_NUMERIC_CELL_RE = re.compile(
    r"^[<>≥≤~±+\-\s]*\d[\d,]*(?:\.\d+)?\s*(?:%|％|[A-Za-zμ°℃·./³²]+(?:\s*[A-Za-zμ°℃·./³²]+)?)?$"
)


def _is_numeric_cell(cell: str) -> bool:
    return bool(_NUMERIC_CELL_RE.match((cell or "").strip()))


def _promote_blank_header(
    headers: list[str], rows: list[list[str]]
) -> tuple[list[str], list[list[str]]]:
    """A table whose header row is blank takes its first body row as the header.

    Word (and PowerPoint) tables carry no header markup, so MarkItDown renders them with an *empty* header
    row and pushes the real one into the body. Left alone that row becomes a bogus data point
    (``项目 / 指标 / 单位`` as a property), the unit column cannot be found — every value loses its unit —
    and classification sees no header text at all.

    Only promoted when the first row looks like labels (no numeric cell) *and* there are numbers below it
    for those labels to head. A genuinely header-less table (``固体含量 | 65 | %``, or ``外观 | 灰色液体``)
    keeps its first row as data.
    """
    if not headers or any(h.strip() for h in headers):
        return headers, rows
    if len(rows) < 2:
        return headers, rows
    first, body = rows[0], rows[1:]
    labels = [c for c in first if c.strip()]
    if not labels or any(_is_numeric_cell(c) for c in labels):
        return headers, rows
    if not any(_is_numeric_cell(c) for row in body for c in row):
        return headers, rows
    return first, body


def _is_borderless_row(line: str) -> bool:
    """单行是否像无框线表格行：2+ 个多空格分隔的列。"""
    stripped = line.strip()
    if not stripped or stripped.startswith("|") or "<table" in stripped.lower():
        return False
    # 2+ 空格分隔
    parts = [p for p in stripped.split("  ") if p.strip()]
    return len(parts) >= 2


def _col_breaks(line: str) -> list[int]:
    """v2 P1 M-5: 列分隔位置（多空格的起始索引），用于对齐校验。"""
    breaks = []
    i = 0
    n = len(line)
    while i < n:
        if line[i] == " " and i + 1 < n and line[i + 1] == " ":
            # 连续空格的起始
            breaks.append(i)
            while i < n and line[i] == " ":
                i += 1
        else:
            i += 1
    return breaks


def _looks_like_borderless_table(lines: list[str], i: int, n: int) -> bool:
    """连续 3+ 行都像无框线表格行，且列分隔位置对齐。

    v2 P1 M-5: 加列对齐校验 —— 此前 3 行双空格散文即被判为表。
    要求至少 2 个分隔位置在 ±3 字符内对齐。
    """
    if i + 2 >= n:
        return False
    if not all(_is_borderless_row(lines[j]) for j in range(i, i + 3)):
        return False
    # 列对齐校验
    breaks_list = [_col_breaks(lines[j]) for j in range(i, i + 3)]
    # 找共同对齐的分隔位置
    aligned = 0
    for b0 in breaks_list[0]:
        # 在其他两行找 ±3 内对齐的
        if any(abs(b0 - b1) <= 3 for b1 in breaks_list[1]) and \
           any(abs(b0 - b2) <= 3 for b2 in breaks_list[2]):
            aligned += 1
    return aligned >= 2


def _borderless_to_pipe(buf: list[str]) -> str | None:
    """无框线文本块 → pipe table（按多空格切分）。"""
    rows = []
    for line in buf:
        parts = [p.strip() for p in line.strip().split("  ") if p.strip()]
        if len(parts) >= 2:
            rows.append(parts)
    if len(rows) < 3:
        return None
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = [
        "| " + " | ".join(rows[0]) + " |",
        "|" + "|".join("---" for _ in rows[0]) + "|",
    ]
    lines.extend("| " + " | ".join(r) + " |" for r in rows[1:])
    return "\n".join(lines)


def blocks_from_markdown(markdown: str) -> list[MdBlock]:
    """Split parser markdown into blocks; pipe/HTML tables become table blocks.

    Page tracking: ``<!-- page:N -->`` markers (what chunking consumes) and the
    raw docling ``<!-- docling-page-break -->`` placeholder. Captions are
    matched nearby: up to 3 text lines before the table, else 1 line after.

    v29 Phase5 L-13: 无框线表格基础检测 —— 连续 3+ 行、2+ 空格分隔、
    列对齐的文本块识别为表格（启发式，fail-open）。
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

        # v29 Phase5 L-13: 无框线表格 —— 连续 3+ 行、多空格分隔对齐
        if _looks_like_borderless_table(lines, i, n):
            buf = [lines[i]]
            j = i + 1
            while j < n and _is_borderless_row(lines[j]):
                buf.append(lines[j])
                j += 1
            # 转为 pipe table 文本
            pipe = _borderless_to_pipe(buf)
            if pipe:
                blocks.append(MdBlock(type="table", page_idx=page_idx, text=pipe))
                table_line_nos.append(i)
                i = j
                continue

        i += 1

    # Caption matching (nearby): up to 3 text lines before, else 1 line after.
    # v3 P1 M-2: 认领仲裁 —— 同一 caption 行不被相邻两表重复认领。
    claimed: set[int] = set()
    for block, start in zip(blocks, table_line_nos):
        for ln_no in range(start - 1, max(start - 4, -1), -1):
            if ln_no in claimed:
                continue
            if 0 <= ln_no < n and lines[ln_no].strip() \
                    and _is_caption_line(lines[ln_no]):
                block.caption = _clean_caption(lines[ln_no])
                claimed.add(ln_no)
                break
        else:
            end_span = len((block.html or block.text).strip().splitlines())
            after = start + end_span
            if after not in claimed and after < n and lines[after].strip() \
                    and _is_caption_line(lines[after]):
                block.caption = _clean_caption(lines[after])
                claimed.add(after)

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

            headers, rows = _promote_blank_header(headers, rows)
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
        # Atomic: the source-detail endpoint reads this file while the ingest that wrote it may still be finishing.
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
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
        payload = json.loads(read_text_with_retry(path))
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
        payload = json.loads(read_text_with_retry(path))
        sets = payload.get("property_sets") or []
        return [dict(s) for s in sets if isinstance(s, dict)]
    except Exception:
        logger.exception("table_contract: load_property_sets failed (fail-open)")
        return []
