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
    """One rasterised page → text, or None. Never raises.

    v29 Phase5 L-7: 置信度标注 —— 用 tsv 获取逐词置信度，
    低置信度（<60）的词标记为 [?词]，提醒下游可能是 OCR 幻觉。
    """
    if not png or not tesseract_available():
        return None
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(png)
            path = f.name
        # 先拿 tsv（含置信度）
        r = subprocess.run(
            ["tesseract", path, "stdout", "-l", lang, "tsv"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        text = _tsv_to_text_with_confidence(r.stdout)
        if text:
            return text
        # tsv 失败时回退纯文本
        r2 = subprocess.run(
            ["tesseract", path, "stdout", "-l", lang],
            capture_output=True,
            text=True,
            timeout=180,
        )
        return r2.stdout.strip() or None
    except Exception as exc:
        return degrade_return(logger, exc, "tesseract page failed", None)
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def _tsv_to_text_with_confidence(tsv: str, min_conf: int = 60) -> str | None:
    """TSV → 文本，低置信度词标记为 [?词]。"""
    lines = tsv.strip().split("\n")
    if len(lines) < 2:
        return None
    words: list[str] = []
    low_count = 0
    for line in lines[1:]:  # 跳过表头
        parts = line.split("\t")
        if len(parts) < 12:
            continue
        # level 5 = word
        if parts[0] != "5":
            continue
        try:
            conf = int(float(parts[10]))
        except (ValueError, IndexError):
            conf = -1
        word = parts[11].strip()
        if not word:
            continue
        if 0 <= conf < min_conf:
            words.append(f"[?{word}]")
            low_count += 1
        else:
            words.append(word)
    if not words:
        return None
    text = " ".join(words)
    if low_count:
        logger.debug("tesseract: %d 低置信度词已标记", low_count)
    return text


def cjk_ratio(text: str) -> float:
    """Fraction of CJK ideographs — the language signal for the router."""
    if not text:
        return 0.0
    return len(_cjk_re().findall(text)) / max(len(text), 1)


def _vote_language(content: bytes, n_pages: int) -> str:
    """v2 P2 M-10: 前 N 页 CJK 占比投票决定语言。"""
    from . import pdf_local

    votes = {"chi_sim": 0, "eng": 0}
    doc = None
    try:
        try:
            doc = pdf_local._open(content)
        except Exception:
            return "eng"
        for page_no in range(1, n_pages + 1):
            png = pdf_local.page_as_png(content, page_no, dpi=80, _doc=doc)
            if not png:
                continue
            # 快速 OCR 一小部分判断语言（用 eng 先跑，统计 CJK）
            text = ocr_png(png, lang="eng")
            del png
            if not text:
                continue
            cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
            if cjk / max(len(text), 1) > 0.1:
                votes["chi_sim"] += 1
            else:
                votes["eng"] += 1
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
    return "chi_sim" if votes["chi_sim"] > votes["eng"] else "eng"


def ocr_pdf(content: bytes, lang: str = "eng", max_pages: int = 30) -> str | None:
    """Every page via Tesseract, assembled with page markers.

    v29 Phase3 M-2: 页数上限（默认 30，与 rapidocr 对齐）——
    此前无上限，超大扫描 PDF 会 OCR 数百页，耗时数小时。

    v2 P2 M-10: lang="auto" 时多页投票 —— 此前只看第一页，
    英文封面+中文正文会被整篇判英文。现采样前 3 页投票。
    """
    if not tesseract_available():
        return None
    from . import pdf_local

    total = pdf_local.page_count(content)
    if total <= 0:
        return None
    _truncated = False
    _orig_total = total
    if total > max_pages:
        logger.warning(
            "tesseract ocr: %d 页超上限 %d，仅处理前 %d 页",
            total, max_pages, max_pages,
        )
        _truncated = True
        total = max_pages

    # v2 P2 M-10: 多页语言投票
    if lang == "auto":
        lang = _vote_language(content, min(total, 3))
        logger.info("tesseract: 多页投票语言=%s", lang)
    rendered: list[tuple[int, str]] = []
    # v2 P2 M-9: 复用 PDF 句柄 —— 一次打开，多页渲染
    doc = None
    try:
        from . import pdf_local as _pl

        try:
            doc = _pl._open(content)
        except Exception:
            doc = None
        for page_no in range(1, total + 1):
            png = pdf_local.page_as_png(content, page_no, dpi=120, _doc=doc)
            if not png:
                continue
            text = ocr_png(png, lang=lang)
            del png
            if text:
                rendered.append((page_no, text))
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
    if not rendered:
        return None
    result = pdf_local.assemble(rendered)
    # v2 P1 M-8: 截断 note —— 超页数时用户可见提示（rapidocr 已有，此处补齐）
    if _truncated:
        note = f"\n\n> 注：原文共 {_orig_total} 页，仅 OCR 前 {max_pages} 页（tesseract 页数上限）。"
        result = (result or "") + note
    return result
