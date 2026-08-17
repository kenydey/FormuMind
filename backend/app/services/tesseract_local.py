"""Tesseract OCR — the fast lane for English scanned documents.

RapidOCR (PP-OCRv4) reads Chinese well but costs ~12-16 s/page on this CPU-only
host, and its bottleneck is the recognition stage (RNN, per text line). English
scans do not need that — Tesseract's `eng` model is far lighter and reads Latin
text in ~7 s/page on the same machine. This module is the fast path the language
router falls back to when a scan turns out to be English.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile

from .errors import degrade_return

logger = logging.getLogger(__name__)

_CJK_RE = None


def _cjk_re():
    import re

    global _CJK_RE
    if _CJK_RE is None:
        _CJK_RE = re.compile(r"[\u4e00-\u9fff]")
    return _CJK_RE


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_png(png: bytes, lang: str = "eng") -> str | None:
    """One rasterised page → text, or None. Never raises."""
    if not png or not tesseract_available():
        return None
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(png)
            path = f.name
        r = subprocess.run(
            ["tesseract", path, "stdout", "-l", lang],
            capture_output=True,
            text=True,
            timeout=180,
        )
        return r.stdout.strip() or None
    except Exception as exc:
        return degrade_return(logger, exc, "tesseract page failed", None)
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def cjk_ratio(text: str) -> float:
    """Fraction of CJK ideographs — the language signal for the router."""
    if not text:
        return 0.0
    return len(_cjk_re().findall(text)) / max(len(text), 1)


def ocr_pdf(content: bytes, lang: str = "eng") -> str | None:
    """Every page via Tesseract, assembled with page markers."""
    if not tesseract_available():
        return None
    from . import pdf_local

    total = pdf_local.page_count(content)
    if total <= 0:
        return None
    rendered: list[tuple[int, str]] = []
    for page_no in range(1, total + 1):
        png = pdf_local.page_as_png(content, page_no, dpi=120)
        if not png:
            continue
        text = ocr_png(png, lang=lang)
        del png
        if text:
            rendered.append((page_no, text))
    if not rendered:
        return None
    return pdf_local.assemble(rendered)
