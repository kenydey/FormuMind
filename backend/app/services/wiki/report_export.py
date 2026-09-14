"""Export dossier reports to DOCX / PDF / PPTX (soft optional deps).

DOCX/PPTX require ``python-docx`` / ``python-pptx``. PDF uses ``fpdf2`` plus a
CJK-capable system font when available (DroidSansFallback / WenQuanYi).
"""
from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CJK_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    # DroidSansFallback lacks many Latin glyphs → avoid as primary.
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/System/Library/Fonts/PingFang.ttc",
)


def export_capabilities() -> dict[str, Any]:
    caps = {"md": True, "docx": False, "pdf": False, "pptx": False, "cjk_font": None}
    try:
        import docx  # noqa: F401

        caps["docx"] = True
    except Exception:
        pass
    try:
        from fpdf import FPDF  # noqa: F401

        caps["pdf"] = True
        caps["cjk_font"] = _find_cjk_font()
    except Exception:
        pass
    try:
        from pptx import Presentation  # noqa: F401

        caps["pptx"] = True
    except Exception:
        pass
    return caps


def _find_cjk_font() -> str | None:
    for p in _CJK_FONT_CANDIDATES:
        if Path(p).is_file():
            return p
    return None


def _strip_fm(markdown: str) -> str:
    text = markdown or ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            return text[end + 4 :].lstrip("\n")
    return text


def _iter_blocks(markdown: str) -> list[tuple[str, str]]:
    """Yield (kind, content) where kind in heading|para|table|hr|list."""
    body = _strip_fm(markdown)
    blocks: list[tuple[str, str]] = []
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.strip() == "---":
            blocks.append(("hr", ""))
            i += 1
            continue
        if line.lstrip().startswith("#"):
            blocks.append(("heading", line.lstrip("# ").strip()))
            i += 1
            continue
        if line.lstrip().startswith("|"):
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(lines[i])
                i += 1
            blocks.append(("table", "\n".join(rows)))
            continue
        if line.lstrip().startswith(("- ", "* ", "> ")):
            items = []
            while i < len(lines) and lines[i].lstrip().startswith(("- ", "* ", "> ")):
                items.append(re.sub(r"^[\s>*\-]+\s*", "", lines[i]))
                i += 1
            blocks.append(("list", "\n".join(items)))
            continue
        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].lstrip().startswith(("#", "|", "-", "*", ">")) and lines[i].strip() != "---":
            para.append(lines[i])
            i += 1
        blocks.append(("para", "\n".join(para)))
    return blocks


def markdown_to_docx(markdown: str, *, title: str = "FormuMind Report") -> bytes:
    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError as exc:
        raise RuntimeError("python-docx not installed") from exc

    doc = Document()
    doc.core_properties.title = title
    doc.add_heading(title, level=0)
    note = doc.add_paragraph("研发草稿 · draft_not_claims · 不得作为 Claims 证据")
    note.runs[0].font.size = Pt(9)

    for kind, content in _iter_blocks(markdown):
        if kind == "heading":
            doc.add_heading(content[:200], level=1)
        elif kind == "para":
            doc.add_paragraph(content)
        elif kind == "list":
            for item in content.splitlines():
                doc.add_paragraph(item, style="List Bullet")
        elif kind == "table":
            rows = [r.strip().strip("|") for r in content.splitlines() if r.strip()]
            parsed = [[c.strip() for c in r.split("|")] for r in rows]
            parsed = [r for r in parsed if r and not all(set(c) <= {"-", ":", " "} for c in r)]
            if not parsed:
                continue
            table = doc.add_table(rows=len(parsed), cols=len(parsed[0]))
            table.style = "Table Grid"
            for ri, row in enumerate(parsed):
                for ci, cell in enumerate(row):
                    if ci < len(table.rows[ri].cells):
                        table.rows[ri].cells[ci].text = cell
        elif kind == "hr":
            doc.add_paragraph("—" * 20)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def markdown_to_pdf(markdown: str, *, title: str = "FormuMind Report") -> bytes:
    try:
        from fpdf import FPDF
    except ImportError as exc:
        raise RuntimeError("fpdf2 not installed") from exc

    font_path = _find_cjk_font()
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(15, 15, 15)
    pdf.add_page()

    use_cjk = False
    if font_path:
        try:
            # Prefer TTF over TTC when possible (already ordered in candidates).
            pdf.add_font("FM", "", font_path)
            pdf.set_font("FM", size=11)
            use_cjk = True
        except Exception as exc:
            logger.warning("CJK font load failed (%s); ASCII fallback", exc)

    if not use_cjk:
        pdf.set_font("Helvetica", size=11)

    def _text(s: str) -> str:
        raw = (s or "").replace("\t", " ")
        if use_cjk:
            return raw
        return raw.encode("ascii", "replace").decode("ascii")

    def _write(s: str, *, size: float = 11, h: float = 6) -> None:
        content = _text(s).strip("\n")
        if not content.strip():
            pdf.ln(h * 0.4)
            return
        # Dual-font: CJK primary if available; Helvetica for pure-ASCII when CJK
        # font lacks Latin (e.g. DroidSansFallback).
        has_non_ascii = any(ord(ch) > 127 for ch in content)
        if use_cjk and (has_non_ascii or font_path.endswith(".ttc")):
            pdf.set_font("FM", size=size)
        elif use_cjk and not has_non_ascii:
            # Prefer Latin-capable face for ASCII-only lines.
            try:
                pdf.set_font("Helvetica", size=size)
            except Exception:
                pdf.set_font("FM", size=size)
        else:
            pdf.set_font("Helvetica", size=size)
        pdf.multi_cell(pdf.epw, h, content)

    _write(title, size=16, h=8)
    _write("draft_not_claims / not Claims evidence", size=9, h=5)
    if use_cjk:
        _write("研发草稿，不得作为 Claims 证据", size=9, h=5)
    pdf.ln(2)

    for kind, content in _iter_blocks(markdown):
        if kind == "heading":
            _write(content[:180], size=13, h=7)
            pdf.ln(1)
        elif kind in ("para", "list"):
            for line in content.splitlines() or [""]:
                prefix = "- " if kind == "list" else ""
                _write(prefix + line[:800], size=11, h=6)
            pdf.ln(1)
        elif kind == "table":
            for line in content.splitlines():
                _write(line[:160], size=8, h=4.5)
            pdf.ln(1)
        elif kind == "hr":
            pdf.ln(2)

    out = pdf.output()
    if isinstance(out, (bytes, bytearray)):
        return bytes(out)
    return str(out).encode("latin-1", errors="ignore")


def _split_slides(markdown: str) -> list[tuple[str, list[str]]]:
    """Split deck markdown on --- into (title, bullet_lines)."""
    body = _strip_fm(markdown)
    chunks = re.split(r"\n---\n", body)
    slides: list[tuple[str, list[str]]] = []
    for chunk in chunks:
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            continue
        title = lines[0].lstrip("# ").strip() or "Slide"
        bullets: list[str] = []
        for ln in lines[1:]:
            if ln.lstrip().startswith("|"):
                continue
            bullets.append(re.sub(r"^[\s#>*\-]+\s*", "", ln).strip())
        slides.append((title, [b for b in bullets if b][:12]))
    if not slides:
        slides = [("FormuMind Deck", ["(empty)"])]
    return slides


def markdown_to_pptx(markdown: str, *, title: str = "FormuMind Deck") -> bytes:
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
    except ImportError as exc:
        raise RuntimeError("python-pptx not installed") from exc

    prs = Presentation()
    prs.core_properties.title = title
    # Title slide
    layout = prs.slide_layouts[0]
    slide = prs.slides.add_slide(layout)
    slide.shapes.title.text = title[:120]
    if slide.placeholders and len(slide.placeholders) > 1:
        slide.placeholders[1].text = "FormuMind · draft_not_claims · 研发草稿"

    bullet_layout = prs.slide_layouts[1]
    for slide_title, bullets in _split_slides(markdown):
        s = prs.slides.add_slide(bullet_layout)
        s.shapes.title.text = slide_title[:120]
        body = s.shapes.placeholders[1].text_frame
        body.clear()
        if not bullets:
            bullets = ["—"]
        for i, b in enumerate(bullets):
            if i == 0:
                body.text = b[:240]
            else:
                p = body.add_paragraph()
                p.text = b[:240]
                p.level = 0
                p.font.size = Pt(18)

    # Disclaimer slide
    end = prs.slides.add_slide(bullet_layout)
    end.shapes.title.text = "溯源声明"
    end.shapes.placeholders[1].text_frame.text = (
        "本演示文稿由 DossierPack 确定性生成；不得把叙述句当作 Claims 证据。"
        "引用请回链 source_ids / 测量行。"
    )

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def export_bytes(
    markdown: str,
    fmt: str,
    *,
    title: str = "FormuMind Report",
) -> tuple[bytes, str, str]:
    """Return (payload, media_type, filename_ext)."""
    kind = (fmt or "md").strip().lower()
    if kind in ("md", "markdown"):
        return (markdown.encode("utf-8"), "text/markdown; charset=utf-8", "md")
    if kind == "docx":
        return (
            markdown_to_docx(markdown, title=title),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "docx",
        )
    if kind == "pdf":
        return (markdown_to_pdf(markdown, title=title), "application/pdf", "pdf")
    if kind in ("pptx", "ppt", "deck"):
        return (
            markdown_to_pptx(markdown, title=title),
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "pptx",
        )
    raise ValueError(f"unsupported export format: {fmt}")
