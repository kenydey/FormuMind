"""Local PDF extraction — the only module that touches PyMuPDF.

Two jobs: turn a PDF into per-page Markdown, and report what each page
*contains* so a caller can decide which pages are worth escalating to a cloud
parser.

**Cost, measured rather than assumed.** pymupdf4llm ≥1.28 activates
`pymupdf.layout`, and that is not free:

    layout on   ~155 MB import, ~350 MB peak, ~176 ms/page
    layout off  ~150 MB import, ~150 MB peak, ~12 ms/page

25× faster and less than half the memory sounds like an easy call, and on a
single-column page the output is identical. It is not an easy call, because on
a two-column page layout-off **interleaves the columns**:

    layout off → "Left column line one about Right column line one about"

Two unrelated sentences merged into one line, which then becomes one chunk and
one embedding. Papers and patents — most of this corpus — are two-column, so
the default stays on and correctness wins. `FORMUMIND_PDF_LAYOUT_ANALYSIS=false`
buys the memory back on a host that truly cannot spare it, at that price.

Peak memory is roughly flat in page count (a 60-page document measured the
same ~350 MB as a 3-page one), so the ceiling is per-request, not per-page.

**Licensing.** PyMuPDF and pymupdf4llm are AGPL-3.0 (or a paid Artifex
licence). Every call to them lives here so that swapping to a permissively
licensed backend later means rewriting one file, not auditing the codebase.

**Why triage lives here.** Nothing else in the repo can rasterise a PDF page
or reach an embedded image, so before this module a "route the figures to the
vision model" pipeline had no way to obtain a figure. `page_as_pdf` and
`page_as_png` are what make that possible.

⚠️ pymupdf4llm's `page_chunks=True` returns only ``metadata`` / ``text`` /
``toc_items`` / ``page_boxes`` — it does **not** report per-page tables or
images, whatever older write-ups suggest. The counts below come from PyMuPDF
directly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import get_settings
from .errors import degrade_return, optional_import

logger = logging.getLogger(__name__)

# A page whose rendered text is shorter than this is treated as having no text
# layer worth using — the usual signature of a scan.
_SCANNED_CHARS_PER_PAGE = 80


@dataclass
class LocalPage:
    """One page as extracted locally, plus what it would cost to give up."""

    page_no: int          # 1-based, matching the page markers the chunker reads
    markdown: str
    char_count: int = 0
    n_tables: int = 0
    n_images: int = 0
    image_area_ratio: float = 0.0
    has_markdown_table: bool = False

    @property
    def looks_scanned(self) -> bool:
        """No usable text layer on this page.

        Means what it says only while `pdf_local_ocr` is off. `char_count` is
        read after `to_markdown` has run, and that call writes its own OCR back
        into the page — so with OCR on, this silently becomes "even after OCR
        there is no text", and real scans stop being recognised as scans.
        """
        return self.char_count < _SCANNED_CHARS_PER_PAGE


def local_available() -> tuple[bool, str]:
    """(usable, actionable Chinese hint) — the shape `vision_available` uses."""
    if not optional_import("pymupdf4llm"):
        return False, (
            "未安装 pymupdf4llm（本地版面解析）。"
            "请在「设置 → 依赖管理」安装，或 pip install pymupdf4llm。"
        )
    return True, ""


def _open(content: bytes):
    import pymupdf  # type: ignore

    return pymupdf.open(stream=content, filetype="pdf")


_layout_configured = False


def _configure_layout() -> None:
    """Pin the layout engine to our setting instead of the library default.

    Set once per process: `use_layout` tears down and rebuilds module globals,
    so calling it per document would be both wasteful and racy.
    """
    global _layout_configured
    if _layout_configured:
        return
    try:
        import pymupdf4llm  # type: ignore

        wanted = bool(get_settings().pdf_layout_analysis)
        if getattr(pymupdf4llm, "_use_layout", None) is not wanted:
            pymupdf4llm.use_layout(wanted)
        _layout_configured = True
    except Exception as exc:  # pragma: no cover - older build without the switch
        logger.debug("pdf_local: layout switch unavailable (%s)", exc)
        _layout_configured = True


def _markdown_kwargs(ocr: bool | None = None) -> dict:
    """Extra `to_markdown` arguments, per configuration.

    Exists for one argument: `use_ocr`. pymupdf4llm's layout parser defaults it
    to **True**, so with an OCR engine on PATH it OCRs every page it judges
    text-poor — for every PDF, in the first tier of the cascade, before anything
    else is consulted. That is where a measured 51.7 s arXiv parse spent ~50 s,
    and it is what a `kb_ingest` run was doing when it hit the 7200 s soft limit.

    Turning it off does not abandon scans: `looks_scanned` routes those to
    `_parse_scanned` (MinerU with OCR) or to local RapidOCR, which is the tier
    built for them and which reports what it did.

    *ocr* overrides the setting, for the one caller that has already established
    the document has no text layer and so genuinely needs it.

    Passed only in layout mode — the legacy parser prints
    "Warning - arguments ignored in legacy mode" for anything it does not know,
    and unknown keywords there are silently dropped rather than honoured.
    """
    settings = get_settings()
    if not settings.pdf_layout_analysis:
        return {}
    return {"use_ocr": bool(settings.pdf_local_ocr if ocr is None else ocr)}


def _page_signals(page) -> tuple[int, int, float]:
    """(tables, images, image area as a fraction of the page).

    Tables *and* images, deliberately. Routing on tables alone — the obvious
    reading of "send the complex pages" — misses chemical structure diagrams
    and reaction schemes entirely, because those are figures.
    """
    import pymupdf  # type: ignore

    try:
        n_tables = len(page.find_tables().tables)
    except Exception:  # pragma: no cover - table finder is best-effort
        n_tables = 0

    image_area = 0.0
    n_images = 0
    try:
        for info in page.get_image_info():
            box = pymupdf.Rect(info["bbox"])
            image_area += abs(box.width * box.height)
            n_images += 1
    except Exception:  # pragma: no cover
        pass

    page_area = abs(page.rect.width * page.rect.height) or 1.0
    return n_tables, n_images, min(image_area / page_area, 1.0)


def extract_pages(content: bytes, *, ocr: bool | None = None) -> list[LocalPage] | None:
    """Per-page Markdown plus triage signals, or None when unavailable.

    Returning None rather than raising keeps this usable as a parser tier: the
    cascade simply moves to the next one.
    """
    available, hint = local_available()
    if not available:
        logger.debug("pdf_local unavailable: %s", hint)
        return None

    _configure_layout()
    try:
        import pymupdf4llm  # type: ignore

        doc = _open(content)
        try:
            chunks = pymupdf4llm.to_markdown(
                doc, page_chunks=True, **_markdown_kwargs(ocr)
            )
            pages: list[LocalPage] = []
            for index, chunk in enumerate(chunks):
                markdown = (chunk.get("text") or "").strip()
                n_tables, n_images, area_ratio = _page_signals(doc[index])
                pages.append(
                    LocalPage(
                        page_no=index + 1,
                        markdown=markdown,
                        char_count=len(doc[index].get_text().strip()),
                        n_tables=n_tables,
                        n_images=n_images,
                        image_area_ratio=area_ratio,
                        # pymupdf4llm renders simple ruled tables as pipe
                        # tables perfectly well. Knowing whether it managed is
                        # what stops us paying a cloud parser to redo them.
                        has_markdown_table="|---" in markdown.replace(" ", ""),
                    )
                )
            return pages
        finally:
            doc.close()
    except Exception as exc:
        return degrade_return(logger, exc, "pdf_local extract failed", None)


def to_markdown(content: bytes) -> str | None:
    """Whole document as Markdown with page markers — the plain parser tier."""
    pages = extract_pages(content)
    if not pages:
        return None
    return assemble([(page.page_no, page.markdown) for page in pages])


def ocr_markdown(content: bytes, *, max_pages: int = 0) -> str | None:
    """The layout parser's own OCR, run deliberately on a document that needs it.

    `pdf_local_ocr` is off because that switch decides whether OCR runs on
    *every* document; it is a separate question from whether OCR should ever
    run. A document with no text layer has nothing else to offer, and this is
    the last reader available without a network or an extra dependency — so on
    a host with Tesseract but no RapidOCR and no reachable MinerU, this is the
    difference between a scan being indexed and being silently dropped.

    Bounded by *max_pages* for the same reason every other OCR path is: one
    200-page scan must not be able to consume an entire ingest run.
    """
    if not get_settings().pdf_layout_analysis:
        # `use_ocr` is a layout-parser argument; the legacy path has no OCR at
        # all and silently discards keywords it does not know. Returning early
        # says so instead of spending a full parse to produce nothing.
        logger.info(
            "pdf_local: local OCR needs layout analysis "
            "(FORMUMIND_PDF_LAYOUT_ANALYSIS=true) — skipping"
        )
        return None
    if max_pages > 0:
        pages_in_doc = page_count(content)
        if pages_in_doc > max_pages:
            logger.warning(
                "pdf_local: %d-page scan exceeds the %d-page local-OCR cap — skipping",
                pages_in_doc, max_pages,
            )
            return None
    pages = extract_pages(content, ocr=True)
    if not pages:
        return None
    return assemble([(page.page_no, page.markdown) for page in pages]) or None


def assemble(pages: list[tuple[int, str]]) -> str:
    """Join per-page Markdown with the page markers the chunker consumes.

    Emitting `<!-- page:N -->` is what lets everything downstream stay
    unchanged: `chunk_markdown` turns these into `DocumentChunk.page_no` and
    strips them, exactly as it already does for docling and pypdf.
    """
    from .chunking import page_marker

    parts: list[str] = []
    for page_no, markdown in pages:
        if not (markdown or "").strip():
            continue
        parts.append(page_marker(page_no))
        parts.append(markdown.strip())
    return "\n\n".join(parts).strip()


def looks_scanned(pages: list[LocalPage]) -> bool:
    """Whether the document as a whole carries no usable text layer.

    Judged over the document, not page by page: a report can legitimately have
    a full-page figure or a cover sheet without being a scan.
    """
    if not pages:
        return True
    return all(page.looks_scanned for page in pages)


def page_as_pdf(content: bytes, page_no: int) -> bytes | None:
    """One page as a single-page PDF, text layer intact.

    Preferred over `page_as_png` for anything born digital. Rasterising throws
    away the vector text and forces a downstream parser to OCR a page whose
    characters were already perfectly available; the single-page PDF costs the
    same one page of quota and arrives with its text.
    """
    try:
        import pymupdf  # type: ignore

        source = _open(content)
        try:
            if not 1 <= page_no <= source.page_count:
                return None
            single = pymupdf.open()
            try:
                single.insert_pdf(source, from_page=page_no - 1, to_page=page_no - 1)
                return single.tobytes()
            finally:
                single.close()
        finally:
            source.close()
    except Exception as exc:
        return degrade_return(logger, exc, f"pdf_local page_as_pdf({page_no}) failed", None)


def page_count(content: bytes) -> int:
    """How many pages, without the layout-analysis pass.

    ``extract_pages`` is the obvious way to learn this and the wrong one: it runs
    pymupdf4llm over the whole document (~350 MB peak, and with an OCR engine
    installed pymupdf4llm will additionally OCR every page). A caller that only
    needs a page loop should not pay for any of that.
    """
    try:
        with _open(content) as doc:
            return int(doc.page_count)
    except Exception as exc:
        return degrade_return(logger, exc, "pdf_local page_count failed", 0)


def page_as_png(content: bytes, page_no: int, dpi: int = 150) -> bytes | None:
    """One page rendered to PNG — for scans, where there is no text to keep."""
    try:
        source = _open(content)
        try:
            if not 1 <= page_no <= source.page_count:
                return None
            pixmap = source[page_no - 1].get_pixmap(dpi=dpi)
            try:
                return pixmap.tobytes("png")
            finally:
                # Pixmaps are the one genuinely large allocation here: an A4
                # page at 150 dpi is ~8 MB of RGBA. Release it rather than
                # waiting for the collector on a 2 GB box.
                del pixmap
        finally:
            source.close()
    except Exception as exc:
        return degrade_return(logger, exc, f"pdf_local page_as_png({page_no}) failed", None)


def embedded_images(content: bytes, page_no: int) -> list[bytes]:
    """Embedded images on a page, as their stored bytes.

    Used to hand figures to the vision model without rasterising the whole
    page — a chemical structure extracted at its native resolution reads far
    better than the same structure cropped out of a 150 dpi render.
    """
    out: list[bytes] = []
    try:
        source = _open(content)
        try:
            if not 1 <= page_no <= source.page_count:
                return []
            for xref, *_ in source[page_no - 1].get_images(full=True):
                try:
                    out.append(source.extract_image(xref)["image"])
                except Exception:  # pragma: no cover - skip unreadable entries
                    continue
            return out
        finally:
            source.close()
    except Exception as exc:
        return degrade_return(logger, exc, f"pdf_local embedded_images({page_no}) failed", [])
