"""Local OCR for scanned PDFs — RapidOCR on ONNX Runtime, CPU only.

Why this exists: a scanned PDF has no text layer, so every text-layer parser in
the cascade returns nothing, and before this the only way to read one was MinerU
— a paid cloud round trip that also has to be enabled. Documents on a deployment
without a MinerU token simply came back as "可能是扫描件" and nothing was indexed.

Why RapidOCR specifically: it is the only OCR option that fits this box. docling
and marker need weights and measure ~54 s/page on CPU (see ``parsing.py``);
local MinerU needs a GPU. ``rapidocr-onnxruntime`` ships the Chinese PP-OCRv4
weights *inside the wheel* (16 MB: det + rec + cls), so ``pip install`` yields
working offline OCR with no model download and nothing to manage at deploy time.

Measured on this hardware (A4, synthetic Chinese page, 2 ORT threads):

    dpi  peak RSS  time    char coverage
    120    372 MB  1.35 s      97 %
    150    557 MB  2.13 s      92 %
    200    659 MB  2.59 s      94 %

Two things in that table drove the defaults. Peak memory scales with pixel
count, not with page count — the engine itself settles back to ~140 MB between
pages — so DPI is the only real lever on a 2.2 GB host. And higher DPI did *not*
read characters better; it fragmented lines (4 real lines became 5, 6, then 7),
so paying for 200 dpi buys worse structure. 150 is the default as a compromise
that leaves headroom for noisier real scans, with the knob exposed.

What this is *not*: a layout parser. RapidOCR returns ``(box, text, score)``, so
there are no HTML tables, no LaTeX equations and no captions. It replaces "the
text of a scanned page", not MinerU's structure — which is why ``hybrid_parse``
still escalates pages that actually contain tables or figures.
"""
from __future__ import annotations

import logging
import re
import threading

from ..config import get_settings
from .errors import degrade_return, optional_import

logger = logging.getLogger(__name__)

_IMPORT_NAME = "rapidocr_onnxruntime"
_PIP_NAME = "rapidocr-onnxruntime"

# The engine costs ~1.2 s to construct and ~125 MB resident. Build it once per
# process: N concurrent uploads would otherwise mean N copies of three ONNX
# sessions, which is how a 2 GB box dies. Same reasoning as the module-level
# converter caches in parsing.py.
_ENGINE = None
_ENGINE_LOCK = threading.Lock()

def _fullwidth_map() -> dict[int, str]:
    """Fullwidth → halfwidth for digits, Latin letters and number punctuation.

    OCR of Chinese text returns these interchangeably — real results were
    ``磷酸锌１5．０份`` for ``磷酸锌 15.0 份`` and ``Ｅ-44`` for the resin grade
    ``E-44``. On a formulation platform those characters *are* the data: a
    quantity and a trade grade that no downstream parser will match. Normalising
    is cheap and there is no case where the fullwidth form is the intended one.

    Deliberately excludes CJK punctuation that carries meaning in prose (、。「」)
    — this is about numbers and identifiers, not rewriting the text.
    """
    table: dict[int, str] = {}
    for full, half in zip("０１２３４５６７８９．％－＋（）：，／＝", "0123456789.%-+():,/="):
        table[ord(full)] = half
    table[0x3000] = " "  # ideographic space — OCR emits it between number and unit
    for offset in range(26):
        table[0xFF21 + offset] = chr(ord("A") + offset)  # Ａ-Ｚ
        table[0xFF41 + offset] = chr(ord("a") + offset)  # ａ-ｚ
    return table


_FULLWIDTH = _fullwidth_map()

_CJK = r"一-鿿　-〿＀-￯"


def rapidocr_available() -> tuple[bool, str]:
    """(usable, actionable hint) — the shape every optional engine here uses."""
    settings = get_settings()
    if not settings.rapidocr_enabled:
        return False, "本地 OCR 已禁用（FORMUMIND_RAPIDOCR_ENABLED）"
    if not optional_import(_IMPORT_NAME):
        return False, (
            f"未安装本地 OCR 引擎（pip install {_PIP_NAME}，或在「设置 → 依赖管理」中安装）"
        )
    return True, ""


def _engine():
    """The cached engine, or None when unavailable."""
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is not None:  # another thread won the race
            return _ENGINE
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore

            threads = max(1, int(get_settings().rapidocr_threads))
            # ONNX Runtime defaults intra_op threads to *every* core. On a
            # 4-core host that means an OCR pass starves the web workers it
            # shares the box with. Thread count does not affect peak memory
            # (measured: 654/657/657 MB at 4/1/2 threads), only latency, so this
            # is purely about not monopolising the CPU.
            _ENGINE = RapidOCR(intra_op_num_threads=threads)
        except Exception as exc:
            return degrade_return(logger, exc, "rapidocr engine init failed", None)
    return _ENGINE


def _line_text(parts: list[tuple[float, str]]) -> str:
    """Join one line's fragments left-to-right, without spacing CJK apart."""
    parts.sort(key=lambda p: p[0])
    out = ""
    for _, txt in parts:
        token = txt.strip()
        if not token:
            continue
        if out and not (
            re.search(f"[{_CJK}]$", out) or re.match(f"^[{_CJK}]", token)
        ):
            out += " "
        out += token
    return out


def _to_reading_order(result) -> str:
    """Detection boxes → text in reading order.

    RapidOCR returns a flat list of boxes with no notion of lines, so anything
    that is visually one row arrives as several entries. Grouping by vertical
    position and sorting by horizontal position within each group is what keeps a
    two-column page or a wrapped row from coming out interleaved.
    """
    rows: list[tuple[float, float, str]] = []
    heights: list[float] = []
    for box, txt, _score in result or []:
        if not (txt or "").strip():
            continue
        ys = [float(p[1]) for p in box]
        xs = [float(p[0]) for p in box]
        rows.append((sum(ys) / len(ys), min(xs), txt))
        heights.append(max(ys) - min(ys))
    if not rows:
        return ""

    # Tolerance from the actual text size rather than a fixed pixel count, so the
    # same code works at any DPI.
    median_h = sorted(heights)[len(heights) // 2] or 1.0
    tol = median_h * 0.6

    rows.sort(key=lambda r: r[0])
    lines: list[list[tuple[float, str]]] = []
    anchors: list[float] = []
    for y, x, txt in rows:
        if anchors and abs(y - anchors[-1]) <= tol:
            lines[-1].append((x, txt))
        else:
            lines.append([(x, txt)])
            anchors.append(y)

    return "\n".join(t for t in (_line_text(ln) for ln in lines) if t)


def ocr_png_scored(png: bytes) -> tuple[str | None, float]:
    """One rasterised page → (text, average confidence 0..1).

    Confidence is the mean of RapidOCR's per-box scores. ``hybrid_parse`` uses it
    to decide whether a scanned page's local OCR is good enough to keep, or
    whether the page should fall back to MinerU for structure.
    """
    ok, _hint = rapidocr_available()
    if not ok or not png:
        return None, 0.0
    engine = _engine()
    if engine is None:
        return None, 0.0
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return None, 0.0
        result, _elapsed = engine(img)
        scores = [float(s) for _, _, s in (result or []) if s is not None]
        avg = (sum(scores) / len(scores)) if scores else 0.0
        text = _to_reading_order(result)
        return (text.translate(_FULLWIDTH) or None), avg
    except Exception as exc:
        return degrade_return(logger, exc, "rapidocr page failed", None), 0.0


def ocr_png(png: bytes) -> str | None:
    """One rasterised page → plain text in reading order, or None.

    Never raises: OCR is one tier of a cascade, and a failure here must fall
    through to the next parser rather than abort an ingest.
    """
    text, _score = ocr_png_scored(png)
    return text


def ocr_pdf(content: bytes) -> str | None:
    """Every page of a PDF via local OCR, assembled with page markers.

    Rasterises one page at a time and lets each PNG go before the next: an A4
    page at 150 dpi is several MB of pixels, and holding all of them would defeat
    the point of a memory-conscious engine.
    """
    ok, _hint = rapidocr_available()
    if not ok:
        return None
    from . import pdf_local

    settings = get_settings()
    # page_count, not extract_pages: the latter runs pymupdf4llm over the whole
    # document (~350 MB peak) and — now that an OCR engine is installed —
    # pymupdf4llm will OCR every page itself on the way. Paying for a full layout
    # analysis to learn a page count, then OCR-ing again, is pure waste.
    total = pdf_local.page_count(content)
    if total <= 0:
        return None
    cap = int(settings.rapidocr_max_pages)
    if total > cap:
        logger.warning("rapidocr: %d-page document capped at %d pages", total, cap)

    dpi = int(settings.rapidocr_dpi)
    rendered: list[tuple[int, str]] = []
    for page_no in range(1, min(total, cap) + 1):
        png = pdf_local.page_as_png(content, page_no, dpi=dpi)
        if not png:
            continue
        text = ocr_png(png)
        del png  # an A4 page at 150 dpi is several MB; do not hold the batch
        if text:
            rendered.append((page_no, text))
    if not rendered:
        return None
    logger.info("rapidocr: %d/%d pages produced text", len(rendered), min(total, cap))
    return pdf_local.assemble(rendered) or None
