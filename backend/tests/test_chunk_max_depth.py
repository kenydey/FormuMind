"""chunk_text recursion depth guard."""
from __future__ import annotations

from app.services.ingestion import _chunk_text


def test_chunk_text_respects_max_depth_without_recursion_error():
    # No paragraph breaks — forces recursive path until max_depth, then hard split.
    long_para = "word " * 5000
    chunks = _chunk_text(long_para, max_chars=400, overlap=50, max_depth=8)
    assert len(chunks) >= 2
    assert all(len(c) <= 400 for c in chunks)
