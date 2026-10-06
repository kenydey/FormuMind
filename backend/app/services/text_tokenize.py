"""Unified BM25/text tokenizer (P1-1).

Single source of truth for tokenization used by:
- ``hybrid_search`` (persistent KB path)
- ``rag`` (chat session RAG path: ``BM25FAISSStore``)
- ``kb_index`` (legacy path)

Chinese is indexed twice over: as jieba cuts it, and as overlapping
character pairs. Every path lowercases and drops punctuation, and gives an
English word or a figure the same token whether or not Chinese surrounds it.
"""
from __future__ import annotations

import logging
import re
import sys
from functools import lru_cache

logger = logging.getLogger(__name__)

_CJK = "\u3400-\u4dbf\u4e00-\u9fff"
_CJK_CHAR = re.compile(f"[{_CJK}]")
_CJK_RUN = re.compile(f"[{_CJK}]+")
# A word is a run of letters/digits of any script; a dotted number stays whole.
_WORD = re.compile(rf"[^\W_{_CJK}]+(?:\.[0-9]+)*")
_WORD_OR_CJK_CHAR = re.compile(rf"[{_CJK}]|[^\W_{_CJK}]+(?:\.[0-9]+)*")
_TOKEN_CACHE_SIZE = 8192


def tokenize(text: str) -> list[str]:
    """Tokenize text for BM25 (unified across all retrieval paths)."""
    text = (text or "").strip().lower()
    if not text:
        return []
    if not _CJK_CHAR.search(text):
        return _WORD.findall(text)
    try:
        import jieba  # noqa: F401 - the cached function imports it; this only decides which cache entry is meant

        with_jieba = True
    except ImportError:
        logger.warning("jieba not installed; falling back to character-level tokenization for Chinese")
        with_jieba = False
    return list(_tokenize_cjk(text, with_jieba))


@lru_cache(maxsize=_TOKEN_CACHE_SIZE)
def _tokenize_cjk(text: str, with_jieba: bool) -> tuple[str, ...]:
    """The tokens of an already lowercased text that holds Chinese."""
    words: list[str] = []
    if with_jieba:
        import jieba

        for piece in jieba.cut(text):
            if _CJK_CHAR.search(piece):
                words.append(piece)
            else:
                words.extend(_WORD.findall(piece))
    else:
        words = _WORD_OR_CJK_CHAR.findall(text)
    for run in _CJK_RUN.findall(text):
        words.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tuple(sys.intern(t) for t in words)
