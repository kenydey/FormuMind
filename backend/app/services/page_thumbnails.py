"""Phase 3 — page thumbnails + VLM chart fallback (opt-in).

Root cause this closes: PDF pages were never rendered to images at ingest,
so chart/figure questions over PDF documents had no visual fallback — the
vision stack (``vision_extract._call_vision``) only ever saw standalone
uploaded images. This module adds:

1. ``maybe_store_page_thumbnails`` — ingest-time, opt-in rendering of PDF
   pages to 40-DPI PNG thumbnails under ``<db_dir>/thumbnails/<source_id>/``
   (same parent as the existing ``attachments/`` local fallback; no new
   facility). Fail-open: any failure is logged and ingest continues.
2. ``answer_chart_question`` — opt-in VLM Q&A: when a question looks like a
   chart/figure question (``is_chart_question``) and ``vlm_fallback_enabled``,
   the stored page thumbnails are fed to the *existing* vision role via
   ``vision_extract._call_vision`` — no new LLM provider abstraction.
3. Cost gating: per-call image token estimates (documented OpenAI vision
   formula) are logged and returned on the result; when disabled the overhead
   is exactly zero (a single boolean check).

Both switches default OFF: ``page_thumbnail_enabled`` and
``vlm_fallback_enabled``.
"""
from __future__ import annotations

import json
import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings

logger = logging.getLogger(__name__)

THUMBNAIL_EXTS = frozenset({"pdf"})

_CHART_KEYWORDS = (
    # 中文
    "图", "图表", "曲线", "柱状", "饼图", "折线", "谱图", "色谱", "显微",
    "照片", "示意图", "流程图",
    # English
    "chart", "figure", "fig.", "plot", "graph", "curve", "diagram",
    "spectrum", "chromatogram", "microscop", "photo", "schematic",
)


@dataclass
class PageThumb:
    page: int  # 1-based
    png: bytes
    width: int
    height: int


@dataclass
class ChartVLMResult:
    answer: str
    pages_used: list[int] = field(default_factory=list)
    image_tokens: int = 0  # estimated input image tokens across calls
    model: str = ""
    hint: str = ""  # non-empty when unavailable instead of raising


def thumbnail_base_dir() -> Path:
    """``<db_dir>/thumbnails`` — mirrors the attachments local-fallback path."""
    settings = get_settings()
    base = Path(settings.db_url.replace("sqlite:///", "")).parent / "thumbnails"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _png_size(png: bytes) -> tuple[int, int]:
    """Read width/height from the PNG IHDR chunk without Pillow."""
    if len(png) >= 24 and png[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", png[16:24])
        return w, h
    return 0, 0


def render_page_thumbnails(content: bytes, dpi: int = 40) -> list[PageThumb]:
    """Render every PDF page to a PNG thumbnail. Fail-open → []."""
    try:
        import fitz  # PyMuPDF; already in the parse chain
    except ImportError:
        logger.warning("page thumbnails skipped: PyMuPDF not installed")
        return []
    try:
        thumbs: list[PageThumb] = []
        with fitz.open(stream=content, filetype="pdf") as doc:
            for i, page in enumerate(doc):
                pix = page.get_pixmap(dpi=dpi)
                png = pix.tobytes("png")
                w, h = _png_size(png)
                thumbs.append(PageThumb(page=i + 1, png=png, width=w, height=h))
        return thumbs
    except Exception as exc:
        logger.warning("page thumbnail render failed (fail-open): %s", exc)
        return []


def store_page_thumbnails(source_id: str, content: bytes, dpi: int = 40) -> int:
    """Render + persist thumbnails for ``source_id``. Returns pages stored."""
    thumbs = render_page_thumbnails(content, dpi=dpi)
    if not thumbs:
        return 0
    dest = thumbnail_base_dir() / source_id
    try:
        dest.mkdir(parents=True, exist_ok=True)
        manifest = {"dpi": dpi, "pages": []}
        for t in thumbs:
            fname = f"p{t.page}.png"
            (dest / fname).write_bytes(t.png)
            manifest["pages"].append(
                {"page": t.page, "file": fname, "width": t.width, "height": t.height}
            )
        (dest / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        logger.info(
            "page thumbnails stored: source=%s pages=%d dpi=%d",
            source_id, len(thumbs), dpi,
        )
        return len(thumbs)
    except OSError as exc:
        logger.warning("page thumbnail store failed (fail-open): %s", exc)
        return 0


def load_page_thumbnails(
    source_id: str, page_nums: list[int] | None = None
) -> list[PageThumb]:
    """Load stored thumbnails (optionally filtered to ``page_nums``)."""
    dest = thumbnail_base_dir() / source_id
    manifest_path = dest / "manifest.json"
    if not manifest_path.exists():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    want = set(page_nums) if page_nums else None
    thumbs: list[PageThumb] = []
    for entry in manifest.get("pages", []):
        if want is not None and entry.get("page") not in want:
            continue
        p = dest / entry.get("file", "")
        if not p.exists():
            continue
        try:
            png = p.read_bytes()
        except OSError:
            continue
        w, h = _png_size(png)
        thumbs.append(PageThumb(page=entry["page"], png=png, width=w, height=h))
    return sorted(thumbs, key=lambda t: t.page)


def maybe_store_page_thumbnails(content: bytes, ext: str, source_id: str | None) -> int:
    """Ingest hook: render thumbnails when opted in. Always fail-open.

    Returns pages stored (0 when disabled / not a PDF / any failure) so the
    caller never needs its own branching.
    """
    settings = get_settings()
    if not settings.page_thumbnail_enabled:
        return 0
    if not source_id or ext.lower() not in THUMBNAIL_EXTS:
        return 0
    try:
        return store_page_thumbnails(source_id, content, dpi=settings.page_thumbnail_dpi)
    except Exception as exc:  # never break ingest
        logger.warning("page thumbnail hook failed (fail-open): %s", exc)
        return 0


def is_chart_question(query: str) -> bool:
    """Heuristic trigger: does the question ask about a chart/figure?"""
    q = (query or "").lower()
    return any(kw in q for kw in _CHART_KEYWORDS)


def estimate_image_tokens(width: int, height: int) -> int:
    """Estimated input tokens for one image (documented OpenAI vision formula).

    Scale to fit within 2048px, then scale so the shortest side is 768px,
    count 512px tiles, 170 tokens/tile + 85 base. A 40-DPI A4 page
    (~331x467) upscales to 768x1083 -> 6 tiles -> 1105 tokens.
    """
    import math

    if width <= 0 or height <= 0:
        return 0
    scale = min(1.0, 2048 / max(width, height))
    w, h = width * scale, height * scale
    scale = 768 / min(w, h)
    w, h = w * scale, h * scale
    tiles = math.ceil(w / 512) * math.ceil(h / 512)
    return tiles * 170 + 85


_CHART_PROMPT = (
    "你是一名化学文献图表解读助手。根据下面这页文档截图回答问题，"
    "只依据图中可见内容作答，看不清或图中没有就明确说明，不要编造数值。\n问题：{query}"
)


def answer_chart_question(
    query: str,
    source_id: str,
    page_nums: list[int] | None = None,
) -> ChartVLMResult | None:
    """Opt-in VLM fallback for chart/figure questions.

    Returns ``None`` when the switch is off (zero overhead) or when the
    question is not chart-like and no pages were requested. Otherwise feeds
    up to ``vlm_max_pages`` stored thumbnails to the existing vision role
    (``vision_extract._call_vision`` — no new provider abstraction) and
    returns the answer together with the estimated image-token cost.
    Fail-open: any failure is logged and ``None`` is returned so the caller
    falls back to the text answer.
    """
    settings = get_settings()
    if not settings.vlm_fallback_enabled:
        return None
    if not is_chart_question(query) and not page_nums:
        return None

    from .vision_extract import _call_vision, vision_available
    from .llm_roles import VISION, resolve_role

    ok, hint = vision_available()
    if not ok:
        logger.info("vlm chart fallback skipped: %s", hint)
        return ChartVLMResult(answer="", hint=hint)

    thumbs = load_page_thumbnails(source_id, page_nums)
    if not thumbs:
        return ChartVLMResult(answer="", hint="该文档没有已入库的页面缩略图")
    thumbs = thumbs[: max(1, settings.vlm_max_pages)]

    cfg = resolve_role(VISION)
    answers: list[str] = []
    pages_used: list[int] = []
    total_tokens = 0
    for t in thumbs:
        tokens = estimate_image_tokens(t.width, t.height)
        try:
            text = _call_vision(
                cfg, _CHART_PROMPT.format(query=query), t.png, f"p{t.page}.png"
            )
        except Exception as exc:
            logger.warning("vlm chart call failed on page %d: %s", t.page, exc)
            continue
        total_tokens += tokens
        pages_used.append(t.page)
        answers.append(f"[第{t.page}页] {text.strip()}")
        logger.info(
            "vlm chart fallback: source=%s page=%d image_tokens~%d model=%s",
            source_id, t.page, tokens, cfg.model,
        )
    if not answers:
        return ChartVLMResult(answer="", hint="视觉问答调用失败，已记录日志")
    return ChartVLMResult(
        answer="\n".join(answers),
        pages_used=pages_used,
        image_tokens=total_tokens,
        model=cfg.model or "",
    )
