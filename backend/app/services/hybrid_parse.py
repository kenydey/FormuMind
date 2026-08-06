"""Hybrid PDF parsing — local for everything, cloud for the pages that need it.

The shape of the problem: a good local parser handles most of a document
perfectly and a few pages badly. Sending the whole file to a metered cloud
parser pays for the pages that were already fine; sending none of it leaves
the dense tables and the chemical structures unreadable. So decide per page.

    ① local extraction        every page, no network, no quota
    ② triage                  which pages did the local pass handle badly?
    ③ escalate those pages    one page at a time, as a single-page PDF
    ④ route the figures       image blocks → vision model → SMILES
    ⑤ reassemble              page markers intact, so nothing downstream changes

**What escalation is worth.** A 50-page report with three table pages costs
three pages of quota, not fifty. `parse` logs the ratio it actually achieved,
because that number is the whole argument for this design and it should be
checkable rather than asserted.

**What goes to the vision model, and what must not.** MinerU returns
`equation` blocks already in LaTeX and `table` blocks already as HTML. Handing
those to a vision model would replace a structured answer with a guess. Only
`image` and `chart` blocks — and tables MinerU itself could not render — have
anything to gain from being looked at.

**On chemical structures.** MinerU does not produce SMILES; it returns the
structure as a picture. The SMILES come from `vision_extract`, which asks the
LLM and then verifies with RDKit. Without RDKit nothing is verified and every
structure is unchecked model output — `image_markdown` renders that as a ✗ in
its own column rather than hiding it.
"""
from __future__ import annotations

import logging

from ..config import get_settings
from . import mineru_cloud, pdf_local
from .errors import degrade_return

logger = logging.getLogger(__name__)

# Block types that arrive structured and must not be second-guessed by a
# vision model, and those that carry no text at all and must be looked at.
_ALREADY_STRUCTURED = frozenset({"text", "title", "list", "code", "equation"})
_VISUAL = frozenset({"image", "chart"})
# Page furniture: repeating it in every chunk is noise that dilutes retrieval.
_DISCARD = frozenset({"header", "footer", "page_number"})


def _needs_escalation(page: pdf_local.LocalPage) -> bool:
    """Whether the local pass left something on this page worth paying for.

    A table the local parser already rendered as a pipe table is *not* worth
    re-parsing — that check is what keeps the quota bill proportional to the
    hard pages rather than to the document.
    """
    settings = get_settings()
    if page.n_tables and not page.has_markdown_table:
        return True
    if page.image_area_ratio >= float(settings.hybrid_image_area_threshold):
        return True
    return False


def _render_blocks(blocks: list[mineru_cloud.MinerUBlock], *, page_label: str) -> str:
    """MinerU blocks → Markdown, routing only what benefits from vision."""
    from .vision_extract import extract_image, image_markdown, vision_available

    can_see, vision_hint = vision_available()
    parts: list[str] = []

    for block in blocks:
        kind = block.type
        if kind in _DISCARD:
            continue

        # Headings come back as `text` blocks carrying a `text_level`, not as a
        # `title` type — verified against the live API, which returns
        # {"type": "text", "text": "…", "text_level": 2} for a document title
        # and never emits "title" at all. Keying on the type alone silently
        # demoted every heading to prose, which cost `heading_path` on every
        # escalated page.
        if block.text_level and block.text.strip():
            level = min(max(block.text_level, 1), 6)
            parts.append(f"{'#' * level} {block.text.strip()}")
            continue

        if kind == "equation":
            # Already LaTeX. A vision model can only do worse.
            if block.text.strip():
                parts.append(f"$$\n{block.text.strip()}\n$$")
            continue

        if kind == "table":
            if block.html.strip():
                # Already structured HTML — keep it verbatim.
                if block.caption:
                    parts.append(f"**{block.caption.strip()}**")
                parts.append(block.html.strip())
                continue
            # A table MinerU could not render is the one table worth a look.
            parts.append(_visual_markdown(block, page_label, can_see, vision_hint,
                                          extract_image, image_markdown))
            continue

        if kind in _VISUAL:
            parts.append(_visual_markdown(block, page_label, can_see, vision_hint,
                                          extract_image, image_markdown))
            continue

        # Everything else — text, list, code, and any block type MinerU adds
        # later — is already prose. An unknown type keeps its text rather than
        # being dropped: silently losing content is the one failure mode with
        # no symptom.
        if block.text.strip():
            parts.append(block.text.strip())

    return "\n\n".join(p for p in parts if p.strip())


def _visual_markdown(
    block: mineru_cloud.MinerUBlock,
    page_label: str,
    can_see: bool,
    vision_hint: str,
    extract_image,
    image_markdown,
) -> str:
    """A figure as Markdown — via the vision model, or degraded but never lost.

    Losing the block would be the worst outcome: a reader of the knowledge base
    would have no way to tell a page with an unreadable figure from a page with
    no figure at all.
    """
    caption = (block.caption or "").strip()
    fallback = f"> [图 · {page_label}]{' ' + caption if caption else ''}"

    if not block.image:
        return fallback + "\n>\n> _（图片数据缺失）_"
    if not can_see:
        return f"{fallback}\n>\n> _（未做视觉解析：{vision_hint}）_"

    try:
        extraction, error = extract_image(block.image, f"{page_label}.png")
    except Exception as exc:  # pragma: no cover - vision layer already degrades
        return degrade_return(
            logger, exc, "hybrid: vision call failed",
            f"{fallback}\n>\n> _（视觉解析异常）_",
        )

    if extraction is None:
        # The reason goes to the log, not into the document. Provider errors
        # carry account identifiers — a real 429 read "Your account
        # org-a752… <ak-fbkd…> is suspended" — and whatever lands here becomes
        # an indexed, searchable chunk. Embedding a vendor's account id in the
        # knowledge base is bad on its own; doing it on every failed figure
        # also fills the index with a few hundred characters of noise that
        # retrieval then has to compete with.
        logger.warning("hybrid: vision returned nothing for %s (%s)", page_label, error)
        return f"{fallback}\n>\n> _（视觉解析未返回结果，详见服务端日志）_"

    rendered = image_markdown(extraction, page_label)
    return f"{caption}\n\n{rendered}" if caption else rendered


def _escalate_page(content: bytes, page: pdf_local.LocalPage) -> str | None:
    """One page through MinerU, or None to keep the local text.

    Sliced as a single-page PDF rather than rendered: the text layer survives,
    so MinerU reads characters instead of OCR-ing pixels, and it costs the same
    one page of quota either way.
    """
    page_pdf = pdf_local.page_as_pdf(content, page.page_no)
    if not page_pdf:
        return None
    document = mineru_cloud.parse_bytes(page_pdf, ext="pdf")
    if document is None or not document.blocks:
        return None
    return _render_blocks(document.blocks, page_label=f"p.{page.page_no}") or None


def _parse_scanned(content: bytes, pages: list[pdf_local.LocalPage]) -> str | None:
    """A document with no text layer: the whole thing, with OCR.

    Per-page escalation makes no sense here — every page would qualify — so
    this is one call, still bounded by the per-document page cap so a long
    scan cannot swallow the day's quota by accident.
    """
    settings = get_settings()
    cap = int(settings.mineru_max_pages_per_doc)
    if len(pages) > cap:
        logger.warning(
            "hybrid: %d-page scan exceeds the %d-page cap — not sending to MinerU",
            len(pages), cap,
        )
        return None

    logger.info("hybrid: no text layer, sending %d pages to MinerU with OCR", len(pages))
    document = mineru_cloud.parse_bytes(content, ext="pdf", ocr=True)
    if document is None or not document.blocks:
        return None

    by_page: dict[int, list[mineru_cloud.MinerUBlock]] = {}
    for block in document.blocks:
        by_page.setdefault(block.page_idx + 1, []).append(block)
    rendered = [
        (page_no, _render_blocks(blocks, page_label=f"p.{page_no}"))
        for page_no, blocks in sorted(by_page.items())
    ]
    return pdf_local.assemble(rendered) or None


def parse(content: bytes) -> str | None:
    """Parse *content*, escalating only the pages that need it.

    Returns None when local extraction is unavailable, so the caller's parser
    cascade moves on. Every other failure degrades to the local result: a
    cloud outage should cost quality, never the upload.
    """
    pages = pdf_local.extract_pages(content)
    if not pages:
        return None

    local_only = pdf_local.assemble([(p.page_no, p.markdown) for p in pages])
    scanned = pdf_local.looks_scanned(pages)

    available, hint = mineru_cloud.mineru_available()
    if not available:
        # A scan has no text layer, so `local_only` here is empty and returning
        # it means the whole document is lost — every tier below this one also
        # reads text layers. Local OCR is the only thing that can read it, and it
        # costs no quota, so try it before giving up.
        if scanned:
            from . import rapidocr_local

            ocr = rapidocr_local.ocr_pdf(content)
            if ocr:
                logger.info("hybrid: scanned document read by local OCR (no cloud)")
                return ocr
        logger.debug("hybrid: local only (%s)", hint)
        return local_only or None

    if scanned:
        # Local first, cloud only where it earns its keep. OCR gets the text of
        # every page for free; MinerU is then worth a call only for pages whose
        # value is structural — a table or figure it can render and OCR cannot,
        # since RapidOCR returns text and boxes with no HTML or LaTeX.
        return _parse_scanned(content, pages) or local_only or None

    cap = int(get_settings().mineru_max_pages_per_doc)
    candidates = [p for p in pages if _needs_escalation(p)]
    selected = candidates[:cap]
    if len(candidates) > cap:
        logger.warning(
            "hybrid: %d pages qualified for escalation, capped at %d — "
            "the rest keep their local text",
            len(candidates), cap,
        )

    if selected:
        # Wake a scale-to-zero vision endpoint once, up front. The loop below is
        # sequential, so only the first call would pay the boot anyway — but
        # paying it here means the log attributes minutes of waiting to a cold
        # start instead of to whichever page happened to go first. A no-op for
        # every provider that is not a rented endpoint.
        from .vision_extract import prewarm

        prewarm()

    upgraded: dict[int, str] = {}
    for page in selected:
        rendered = _escalate_page(content, page)
        if rendered:
            upgraded[page.page_no] = rendered

    logger.info(
        "hybrid: %d/%d pages escalated (%d succeeded)",
        len(selected), len(pages), len(upgraded),
    )
    # Surfaced on the per-document ingest line too. The parser tier is recorded
    # as "hybrid" whether or not MinerU was called, so without this there is no
    # per-document way to tell whether the quota bought anything — which is the
    # first question anyone asks after paying to turn it on.
    if upgraded:
        from . import ingest_timing as timing

        timing.note(mineru_pages=len(upgraded))
    return pdf_local.assemble(
        [(p.page_no, upgraded.get(p.page_no, p.markdown)) for p in pages]
    ) or None
