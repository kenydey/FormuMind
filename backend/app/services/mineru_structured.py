"""MinerU first-class backend: whole-document cloud parse with structured output.

Why this module exists (root-cause note, 2026-09-28): the local MinerU
pipeline (``magic_pdf``) is not installed in this deployment, so the
``mineru`` tier in ``parsing.py`` is dead code here — the v3 plan's
"medium effort / mfr_enable" finding describes the *local* pipeline and does
not apply. The MinerU that actually works here is the **cloud** API
(``mineru_cloud.py``), and its own silent-off trap was different: the SDK only
sends ``enable_formula``/``enable_table`` when explicitly set, and the old
call never set them. This tier sets them explicitly and logs the effective
options, which is the cloud equivalent of the mfr_enable assertion.

What this tier adds over the existing ``hybrid`` tier:

* ``hybrid`` = local-first with per-page cloud escalation, automatic.
* ``mineru_cloud`` tier = explicit opt-in whole-document high-fidelity parse
  (``prefer="mineru_cloud"`` / ``FORMUMIND_PDF_PARSER=mineru_cloud``),
  with formula/table recognition forced on and structured products
  (tables, formulas, typed blocks) persisted to the Phase-0 tables.

Page-quality adaptation (DeepDoc-inspired): before paying for a cloud call,
each page's text layer is probed locally with fitz. A document whose pages
are all clean skips the cloud call entirely (returns None → fail-open to the
next tier) — the detection models never run on pages that do not need them.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field

from ..config import get_settings
from .errors import log_handled_exception

logger = logging.getLogger(__name__)

# ── structured products ──────────────────────────────────────────────────


@dataclass
class StructuredBlock:
    """One layout block with its kind, replacing chunker heuristics."""

    page_no: int | None
    kind: str  # text | table | formula | figure | image
    text: str = ""
    html: str = ""  # table body HTML (kind == "table")
    caption: str = ""
    latex: str = ""  # kind == "formula"
    formula_no: str | None = None
    # Passed through from MinerUBlock when the cloud content_list carries a
    # bbox; None otherwise (bbox presence in content_list is unverified —
    # see MinerUBlock). Never assert absence.
    bbox: list | None = None


@dataclass
class MinerUStructured:
    markdown: str
    blocks: list[StructuredBlock] = field(default_factory=list)

    @property
    def tables(self) -> list[StructuredBlock]:
        return [b for b in self.blocks if b.kind == "table"]

    @property
    def formulas(self) -> list[StructuredBlock]:
        return [b for b in self.blocks if b.kind == "formula"]


# ── formula gatekeeper (Phase 1, §8.4) ────────────────────────────────────
#
# "Main force + gatekeeper": MinerU's MFR is the main force; this only handles
# what it misses. Two triggers, both counted, LLM off by default.

_FORMULA_INLINE_RE = re.compile(
    r"\\ce\{[^}]{1,200}\}"
    r"|\\begin\{equation\*?\}"
    r"|\\begin\{align\*?\}"
    r"|\$[^$\n]{3,200}\$"
)

_GATEKEEPER_LOCK = threading.Lock()
_gatekeeper_stats: dict[str, int] = {
    "empty_latex": 0,  # equation block whose LaTeX came back empty
    "regex_catch": 0,  # \ce{}/\begin{equation} in text MinerU did not mark
    "llm_fixed": 0,  # gatekeeper LLM corrections applied
}


def gatekeeper_stats() -> dict[str, int]:
    with _GATEKEEPER_LOCK:
        return dict(_gatekeeper_stats)


def reset_gatekeeper_stats() -> None:  # tests only
    with _GATEKEEPER_LOCK:
        for k in _gatekeeper_stats:
            _gatekeeper_stats[k] = 0


def _bump(stat: str, n: int = 1) -> None:
    with _GATEKEEPER_LOCK:
        _gatekeeper_stats[stat] = _gatekeeper_stats.get(stat, 0) + n


@dataclass
class GatekeeperReport:
    empty_latex_blocks: int = 0
    regex_catches: int = 0
    llm_fixed: int = 0


def formula_gatekeeper(structured: MinerUStructured) -> GatekeeperReport:
    """Count (and optionally LLM-fix) formula blocks MinerU fumbled.

    Default is count-only: ``formula_gatekeeper_llm_enabled`` gates the
    single-block DeepSeek correction calls. Never raises — the gatekeeper
    must not fail a parse it was meant to improve.
    """
    report = GatekeeperReport()
    try:
        empty = [b for b in structured.formulas if not (b.latex or "").strip()]
        if empty:
            report.empty_latex_blocks = len(empty)
            _bump("empty_latex", len(empty))

        marked = {id(b) for b in structured.formulas}
        catches = 0
        for b in structured.blocks:
            if id(b) in marked or b.kind != "text":
                continue
            if _FORMULA_INLINE_RE.search(b.text or ""):
                catches += 1
        if catches:
            report.regex_catches = catches
            _bump("regex_catch", catches)

        if report.empty_latex_blocks or report.regex_catches:
            logger.info(
                "formula gatekeeper: %d empty-latex blocks, %d regex catches "
                "(llm=%s)",
                report.empty_latex_blocks,
                report.regex_catches,
                bool(get_settings().formula_gatekeeper_llm_enabled),
            )

        if get_settings().formula_gatekeeper_llm_enabled and (
            report.empty_latex_blocks or report.regex_catches
        ):
            fixed = _llm_fix_formulas(structured, empty)
            report.llm_fixed = fixed
            if fixed:
                _bump("llm_fixed", fixed)
    except Exception as exc:
        log_handled_exception(logger, exc, "formula gatekeeper failed")
    return report


def _llm_fix_formulas(
    structured: MinerUStructured, empty: list[StructuredBlock]
) -> int:
    """One DeepSeek call per fumbled block: normalise to LaTeX/SMILES."""
    try:
        from . import llm as _llm
    except Exception:
        return 0
    fixed = 0
    for b in empty:
        prompt = (
            "下面是一段从化学文献/专利 PDF 中提取的公式区域文本，OCR 可能有乱码。"
            "请输出规范化的 LaTeX（行内公式）或 SMILES（分子式），只输出结果本身，"
            "不要解释：\n\n" + (b.text or "")[:2000]
        )
        try:
            out = _llm.complete_json(
                prompt, model=None
            )
        except Exception:
            continue
        latex = ""
        if isinstance(out, dict):
            latex = str(out.get("latex") or out.get("result") or "").strip()
        if latex:
            b.latex = latex
            fixed += 1
    return fixed


# ── page-quality probe (adaptive skip) ────────────────────────────────────


def _page_text_lengths(content: bytes) -> list[int] | None:
    """Chars of extractable text per page, via fitz. None if unavailable."""
    try:
        import fitz  # type: ignore
    except ImportError:
        return None
    try:
        doc = fitz.open(stream=content, filetype="pdf")
        out = [len((page.get_text() or "").strip()) for page in doc]
        doc.close()
        return out
    except Exception:
        return None


def _doc_is_clean(content: bytes) -> bool:
    """True when every page already has a usable text layer.

    "Clean" = each page yields >= ``mineru_cloud_min_chars_per_page`` chars.
    A clean document gains nothing from a cloud re-parse, so the tier returns
    None and the cascade keeps its free local result.
    """
    settings = get_settings()
    if not settings.mineru_cloud_adaptive_skip:
        return False
    lengths = _page_text_lengths(content)
    if not lengths:
        return False  # cannot judge → do not skip
    threshold = int(settings.mineru_cloud_min_chars_per_page)
    clean = all(n >= threshold for n in lengths)
    if clean:
        logger.info(
            "mineru_cloud: %d pages all clean (>=%d chars) — skipping cloud call",
            len(lengths),
            threshold,
        )
    return clean


# ── block → markdown rendering ────────────────────────────────────────────

_KIND_TO_BLOCK = {
    "text": "text",
    "title": "text",
    "table": "table",
    "equation": "formula",
    "image": "figure",
    "figure": "figure",
    "chart": "figure",
}


def _normalise_blocks(document) -> list[StructuredBlock]:
    blocks: list[StructuredBlock] = []
    for raw in document.blocks:
        kind = _KIND_TO_BLOCK.get((raw.type or "").lower(), "text")
        text = raw.text or ""
        latex = ""
        formula_no = None
        if kind == "formula":
            latex = text.strip()
            m = re.search(r"\(([\d.\-–]+)\)\s*$", latex)
            if m:
                formula_no = m.group(1)
        blocks.append(
            StructuredBlock(
                page_no=(raw.page_idx or 0) + 1,
                kind=kind,
                text=text,
                html=raw.html or "",
                caption=raw.caption or "",
                latex=latex,
                formula_no=formula_no,
                bbox=raw.bbox,  # pass-through when present, else None
            )
        )
    return blocks


def _html_table_to_markdown(html: str) -> str:
    """Minimal HTML-table → pipe-table converter (no new dependencies)."""
    from html.parser import HTMLParser

    rows: list[list[str]] = []
    current_row: list[str] = []
    current_cell: list[str] = []
    in_cell = False
    # v24-fix: colspan/rowspan 展开 —— 合并单元格按属性展开，空位填 ""。
    # v25-fix: 新单元格填首个未被 rowspan 占用的列索引，而非 append（非首列 rowspan 错位）。
    pending_rowspans: list[tuple[int, int, str]] = []  # (col_idx, remaining_rows, text)
    occupied: set[int] = set()  # 本行已被 rowspan 占用的列索引
    current_colspan = 1
    current_rowspan = 1
    table_depth = 0  # v27 P2-14: 嵌套 <table> 计数

    def _flush_cell() -> None:
        """把当前单元格写入行（v27 P2-15 抽取，供隐式闭合复用）。"""
        nonlocal in_cell
        in_cell = False
        _text = "".join(current_cell).strip()
        # v25-fix: 填首个未被 rowspan 占用的列，而非 append 到行尾。
        _col_idx = 0
        while _col_idx in occupied:
            _col_idx += 1
        # colspan: 横向展开
        for _i in range(current_colspan):
            while len(current_row) <= _col_idx + _i:
                current_row.append("")
            current_row[_col_idx + _i] = _text if _i == 0 else ""
            occupied.add(_col_idx + _i)
        # rowspan: 记录到 pending，下一行填充
        if current_rowspan > 1:
            for _i in range(current_colspan):
                pending_rowspans.append((_col_idx + _i, current_rowspan - 1, _text if _i == 0 else ""))

    class _P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            nonlocal in_cell, current_colspan, current_rowspan, occupied, table_depth
            if tag == "table":
                # v27 P2-14: 嵌套表格 —— 内层事件全部忽略，只处理最外层。
                table_depth += 1
                return
            if table_depth != 1:
                return
            if tag == "tr":
                # v27 P2-15: 未闭合 <td> 在行结束时隐式闭合（浏览器语义）。
                if in_cell:
                    _flush_cell()
                current_row.clear()
                # 应用上一行的 rowspan 占位
                occupied = set()
                _new_pending = []
                for _cidx, _rem, _txt in pending_rowspans:
                    while len(current_row) <= _cidx:
                        current_row.append("")
                    current_row[_cidx] = _txt
                    occupied.add(_cidx)
                    if _rem > 1:
                        _new_pending.append((_cidx, _rem - 1, _txt))
                pending_rowspans.clear()
                pending_rowspans.extend(_new_pending)
            elif tag in ("td", "th"):
                # v27 P2-15: 新 <td> 开始时若上一个未闭合，先隐式闭合（浏览器语义）。
                if in_cell:
                    _flush_cell()
                in_cell = True
                current_cell.clear()
                _attrs = dict(attrs)
                try:
                    current_colspan = max(1, int(_attrs.get("colspan", 1)))
                except (ValueError, TypeError):
                    current_colspan = 1
                try:
                    current_rowspan = max(1, int(_attrs.get("rowspan", 1)))
                except (ValueError, TypeError):
                    current_rowspan = 1

        def handle_endtag(self, tag):
            nonlocal in_cell, table_depth
            if tag == "table":
                table_depth = max(0, table_depth - 1)
                return
            if table_depth != 1:
                return
            if tag in ("td", "th"):
                _flush_cell()
            elif tag == "tr":
                # v27 P2-15: 行结束时残留单元格隐式闭合。
                if in_cell:
                    _flush_cell()
                rows.append(list(current_row))

        def handle_data(self, data):
            # v27 P2-14: 嵌套表格内的文本不混入外层单元格。
            if in_cell and table_depth == 1:
                current_cell.append(data)

    try:
        _P().feed(html or "")
    except Exception:
        return ""
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |"]
    lines.append("| " + " | ".join("---" for _ in rows[0]) + " |")
    for r in rows[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _html_table_shape(html: str) -> tuple[int | None, int | None]:
    """(n_rows, n_cols) from table HTML, computed locally.

    No MinerU dependency: the shape is derived from the HTML we already
    have. Returns (None, None) when the HTML carries no parseable rows.
    colspan is expanded (v25-fix: 与 _html_table_to_markdown 展开后的列数一致)；
    rowspan 不增加行数。
    """
    from html.parser import HTMLParser

    rows: list[int] = []
    in_row = False
    in_cell = False
    cell_count = 0
    table_depth = 0  # v27 P2-14: 嵌套 <table> 计数，只统计最外层

    class _P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            nonlocal in_row, in_cell, cell_count, table_depth
            if tag == "table":
                table_depth += 1
                return
            if table_depth != 1:
                return
            if tag == "tr":
                in_row = True
                cell_count = 0
            elif tag in ("td", "th") and in_row:
                if not in_cell:
                    in_cell = True
                    # v25-fix: colspan 展开计数，与 markdown 实际列数一致。
                    _attrs = dict(attrs)
                    try:
                        _cs = max(1, int(_attrs.get("colspan", 1)))
                    except (ValueError, TypeError):
                        _cs = 1
                    cell_count += _cs

        def handle_endtag(self, tag):
            nonlocal in_row, in_cell, table_depth
            if tag == "table":
                table_depth = max(0, table_depth - 1)
                return
            if table_depth != 1:
                return
            if tag in ("td", "th"):
                in_cell = False
            elif tag == "tr":
                if in_row:
                    rows.append(cell_count)
                in_row = False

    try:
        _P().feed(html or "")
    except Exception:
        return None, None
    if not rows:
        return None, None
    return len(rows), max(rows)


def render_markdown(blocks: list[StructuredBlock]) -> str:
    """Blocks → Markdown with page + block-type markers for the chunker."""
    from .chunking import block_marker, page_marker

    out: list[str] = []
    last_page: int | None = None
    for b in blocks:
        if b.page_no != last_page:
            out.append(page_marker(b.page_no or 1))
            last_page = b.page_no
        out.append(block_marker(b.kind))
        if b.kind == "table":
            md_table = _html_table_to_markdown(b.html)
            body = md_table or b.text
            if b.caption:
                out.append(f"表注：{b.caption}")
            out.append(body)
        elif b.kind == "formula":
            out.append(b.latex or b.text)
        elif b.kind in ("figure", "image"):
            out.append(f"[图：{b.caption or '见原文插图'}]")
        else:
            out.append(b.text)
    return "\n\n".join(p for p in out if p.strip())


# ── entry point ────────────────────────────────────────────────────────────


def parse_structured(content: bytes) -> MinerUStructured | None:
    """Whole-document MinerU cloud parse with formula/table forced on.

    Returns None when the cloud is unavailable, the document is clean
    (adaptive skip), or the call fails — the caller falls through to the
    next tier. Never raises.
    """
    from . import mineru_cloud

    available, hint = mineru_cloud.mineru_available()
    if not available:
        logger.debug("mineru_cloud tier: unavailable (%s)", hint)
        return None
    if _doc_is_clean(content):
        return None
    try:
        document = mineru_cloud.parse_bytes(
            content,
            ext="pdf",
            formula=True,  # explicit: SDK only sends when set (§8.2 trap)
            table=True,
        )
    except Exception as exc:
        log_handled_exception(logger, exc, "mineru_cloud structured parse failed")
        return None
    if document is None or not document.blocks:
        return None
    blocks = _normalise_blocks(document)
    structured = MinerUStructured(markdown=render_markdown(blocks), blocks=blocks)
    formula_gatekeeper(structured)  # count-only unless explicitly enabled
    return structured


def persist_structured(source_id: str, structured: MinerUStructured) -> None:
    """Write tables/formulas to the Phase-0 tables. Fail-open by contract."""
    try:
        from ..db.database import default_session_factory
        from ..db.extraction_store import ExtractionStore

        store = ExtractionStore(default_session_factory())
        tables = []
        for b in structured.tables:
            n_rows, n_cols = _html_table_shape(b.html)
            tables.append(
                {
                    "page_no": b.page_no,
                    "bbox": b.bbox,
                    "caption": b.caption or None,
                    "markdown_text": _html_table_to_markdown(b.html) or b.text,
                    "n_rows": n_rows,
                    "n_cols": n_cols,
                }
            )
        formulas = [
            {
                "page_no": b.page_no,
                "bbox": b.bbox,
                "latex": b.latex or b.text,
                "formula_no": b.formula_no,
            }
            for b in structured.formulas
        ]
        if tables:
            store.replace_tables(source_id, tables)
        if formulas:
            store.replace_formulas(source_id, formulas)
        logger.info(
            "mineru_cloud: persisted %d tables, %d formulas for source %s",
            len(tables),
            len(formulas),
            source_id,
        )
    except Exception:
        logger.exception("mineru_cloud: structured persist failed (fail-open)")
