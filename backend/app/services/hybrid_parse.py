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

# ── vision circuit breaker ────────────────────────────────────────────────
# Per-document counter: how many consecutive vision calls have failed with a
# *permanent* error (endpoint paused, token rejected).  Reset at the start of
# every ``parse()`` call so a broken endpoint on one document does not leak
# into the next.  Transient failures (timeout, 503 cold-start) do NOT
# increment this counter — retrying may succeed.
_vision_consecutive_failures = 0


def _needs_escalation(page: pdf_local.LocalPage) -> bool:
    """Whether the local pass left something on this page worth paying for.

    A table the local parser already rendered as a pipe table is *not* worth
    re-parsing — that check is what keeps the quota bill proportional to the
    hard pages rather than to the document.

    A page with no text layer at all is the worst-handled page there is, and it
    only reaches here in a *mixed* document: `looks_scanned` is `all()` over the
    pages, so a file with one text cover sheet and twenty scans is not "a scan"
    and never sees the scanned branch. While the layout parser OCR-ed every
    document those pages were quietly filled in; with that off they would come
    back empty, which is the failure mode with no symptom.
    """
    settings = get_settings()
    if page.looks_scanned:
        return True
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
    global _vision_consecutive_failures

    caption = (block.caption or "").strip()
    fallback = f"> [图 · {page_label}]{' ' + caption if caption else ''}"

    if not block.image:
        return fallback + "\n>\n> _（图片数据缺失）_"
    if not can_see:
        return f"{fallback}\n>\n> _（未做视觉解析：{vision_hint}）_"

    # ── circuit breaker: skip when the endpoint has been telling us no ──
    settings = get_settings()
    budget = int(settings.vision_max_consecutive_failures)
    if budget and _vision_consecutive_failures >= budget:
        logger.info(
            "hybrid: vision breaker open after %d consecutive permanent "
            "failures — skipping remaining figures in this document",
            _vision_consecutive_failures,
        )
        return (
            f"{fallback}\n>\n>"
            f" _（视觉解析已跳过：端点连续 {_vision_consecutive_failures} 次返回不可恢复错误）_"
        )

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
        # Permanent failures trip the breaker; transient ones keep trying.
        from .vision_extract import _is_permanent_vision_failure

        if _is_permanent_vision_failure(error):
            _vision_consecutive_failures += 1
            # Quota exhaustion (429 insufficient_quota) is not going to recover
            # until the vendor resets the weekly quota — skip the "wait N pages"
            # ramp and open the breaker immediately so every remaining figure in
            # this document degrades to a placeholder instead of retrying 3× each.
            lower = (error or "").lower()
            if "insufficient_quota" in lower or "quota has been exhausted" in lower:
                _vision_consecutive_failures = max(_vision_consecutive_failures, budget)
                logger.info("hybrid: vision quota exhausted — breaker opened immediately")
            logger.info(
                "hybrid: vision permanent failure #%d for %s — "
                "breaker at %d; %s",
                _vision_consecutive_failures, page_label, budget, error,
            )
        return f"{fallback}\n>\n> _（视觉解析未返回结果，详见服务端日志）_"

    # A successful call resets the counter: the endpoint is reachable again.
    _vision_consecutive_failures = 0
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
    document = mineru_cloud.parse_bytes(
        page_pdf, ext="pdf", timeout=float(get_settings().mineru_page_timeout_s)
    )
    if document is None or not document.blocks:
        return None
    return _render_blocks(document.blocks, page_label=f"p.{page.page_no}") or None


def _escalate_pages_batch(
    content: bytes, pages: list[pdf_local.LocalPage]
) -> dict[int, str]:
    """All qualifying non-scanned pages in one batch MinerU submission.

    Sliced as single-page PDFs (text layer intact, so MinerU reads characters
    instead of OCR-ing pixels), submitted in one ``/file-urls/batch`` call so the
    server parses them in parallel, then rendered page by page through the same
    fusion layer as the single-page path. A page whose slice failed or whose
    batch result is missing keeps its local text — identical degradation to the
    serial path, just reached with one round trip instead of N.
    """
    page_pdfs: list[bytes] = []
    order: list[int] = []
    for page in pages:
        page_pdf = pdf_local.page_as_pdf(content, page.page_no)
        if not page_pdf:
            continue
        page_pdfs.append(page_pdf)
        order.append(page.page_no)
    if not page_pdfs:
        return {}

    documents = mineru_cloud.parse_pages_batch(page_pdfs)
    upgraded: dict[int, str] = {}
    for page_no, document in zip(order, documents):
        if document is None or not document.blocks:
            continue
        rendered = _render_blocks(document.blocks, page_label=f"p.{page_no}")
        if rendered:
            upgraded[page_no] = rendered
    return upgraded


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


def _scanned_without_cloud(content: bytes) -> str | None:
    """Read a document with no text layer, using only what is on this host.

    Language-routed: English scans go to Tesseract (fast, ~7 s/page), Chinese
    scans to RapidOCR (accurate, ~12 s/page). Language is judged from the first
    page's RapidOCR output by its CJK ratio, so the extra cost is one page of
    RapidOCR (~12 s) against a whole-document saving on English scans. RapidOCR
    stays the backstop when Tesseract is absent or cannot read the document.
    """
    from . import rapidocr_local, tesseract_local

    if tesseract_local.tesseract_available():
        first_png = pdf_local.page_as_png(
            content, 1, dpi=int(get_settings().rapidocr_dpi)
        )
        if first_png:
            first_text, _conf = rapidocr_local.ocr_png_scored(first_png)
            del first_png
            if first_text and tesseract_local.cjk_ratio(first_text) < 0.1:
                eng = tesseract_local.ocr_pdf(content, lang="eng")
                if eng:
                    logger.info("hybrid: scanned document read by Tesseract (English)")
                    return eng

    text = rapidocr_local.ocr_pdf(content)
    if text:
        logger.info("hybrid: scanned document read by local OCR (no cloud)")
        return text

    # Same page cap as RapidOCR on purpose: this is the same job by another
    # engine, and a 200-page scan must not be able to swallow an ingest run
    # merely because the first reader was unavailable.
    text = pdf_local.ocr_markdown(
        content, max_pages=int(get_settings().rapidocr_max_pages)
    )
    if text:
        logger.info("hybrid: scanned document read by the layout parser's own OCR")
    return text


def parse(content: bytes) -> str | None:
    """Parse *content*, escalating only the pages that need it.

    Returns None when local extraction is unavailable, so the caller's parser
    cascade moves on. Every other failure degrades to the local result: a
    cloud outage should cost quality, never the upload.
    """
    global _vision_consecutive_failures
    _vision_consecutive_failures = 0

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
            ocr = _scanned_without_cloud(content)
            if ocr:
                return ocr
        else:
            # Mixed document, no cloud parser: the text-layer-less pages have
            # nowhere to go. Escalation is what normally rescues them and it
            # needs MinerU. Say so per document rather than shipping a
            # half-empty one silently — the pages are gone either way, but an
            # operator can act on a number.
            blank = sum(1 for p in pages if p.looks_scanned)
            if blank:
                logger.warning(
                    "hybrid: %d/%d pages have no text layer and no parser can "
                    "read them (MinerU off: %s; enable it or "
                    "FORMUMIND_PDF_LOCAL_OCR)",
                    blank, len(pages), hint,
                )
        logger.debug("hybrid: local only (%s)", hint)
        return local_only or None

    if scanned:
        # 本地 OCR 优先：扫描页无文本层，本地 RapidOCR 免费且快（秒级/页），
        # 而 MinerU OCR 烧配额、耗时长（上传→轮询→下载）且会触发视觉模型调用。
        # 本地读得出就用本地，读不出才回退 MinerU —— 与混合文档的升级循环一致。
        ocr = _scanned_without_cloud(content)
        if ocr:
            return ocr
        return (
            _parse_scanned(content, pages)
            or local_only
            or None
        )

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

    # The per-page paths (scanned pages, and the batch-off rollback below) are
    # sequential, so a failure is not free — it costs the full per-page timeout,
    # and an unreachable MinerU charges that once per page. The breaker turns
    # "20 pages × one broken network" into three attempts. Counted
    # *consecutively*: a document where some pages come back keeps going,
    # because that is a working connection with a few hard pages. The batch
    # path needs no breaker — a broken network costs it one timeout, not N.
    budget = int(get_settings().mineru_max_page_failures)
    min_conf = float(get_settings().rapidocr_min_confidence)
    upgraded: dict[int, str] = {}
    attempted = 0
    consecutive_failures = 0

    scanned_sel = [p for p in selected if p.looks_scanned]
    cloud_sel = [p for p in selected if not p.looks_scanned]

    # ① 扫描页：本地 OCR 优先（快、免费、省配额），低置信度或读不出才回退
    # MinerU 单页结构化解析。逐页串行 + 熔断保留——扫描页在本语料里罕见。
    for page in scanned_sel:
        attempted += 1
        from . import rapidocr_local

        png = pdf_local.page_as_png(
            content, page.page_no, dpi=int(get_settings().rapidocr_dpi)
        )
        if png:
            text, conf = rapidocr_local.ocr_png_scored(png)
            del png
            if text and conf >= min_conf:
                upgraded[page.page_no] = text
                consecutive_failures = 0
                logger.info(
                    "hybrid: p.%d read by local OCR (conf %.2f ≥ %.2f)",
                    page.page_no, conf, min_conf,
                )
                continue
        rendered = _escalate_page(content, page)
        if rendered:
            upgraded[page.page_no] = rendered
            consecutive_failures = 0
            continue
        consecutive_failures += 1
        if budget and consecutive_failures >= budget:
            logger.warning(
                "hybrid: %d consecutive MinerU page failures — abandoning "
                "escalation after %d/%d pages; the rest keep their local text",
                consecutive_failures, attempted, len(selected),
            )
            break

    # ② 非扫描页（密集表格/图）：一次批量提交，服务端并行；开关关闭退回逐页串行。
    if cloud_sel:
        if get_settings().mineru_batch_enabled:
            attempted += len(cloud_sel)
            upgraded.update(_escalate_pages_batch(content, cloud_sel))
        else:
            for page in cloud_sel:
                attempted += 1
                rendered = _escalate_page(content, page)
                if rendered:
                    upgraded[page.page_no] = rendered
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if budget and consecutive_failures >= budget:
                        logger.warning(
                            "hybrid: %d consecutive MinerU page failures — abandoning "
                            "escalation after %d/%d pages; the rest keep their local text",
                            consecutive_failures, attempted, len(selected),
                        )
                        break

    # `attempted`, not `len(selected)`: once the breaker can cut the loop short,
    # reporting the plan instead of the work would make this line a lie about
    # what the quota actually bought.
    logger.info(
        "hybrid: %d/%d pages escalated (%d succeeded)",
        attempted, len(pages), len(upgraded),
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
