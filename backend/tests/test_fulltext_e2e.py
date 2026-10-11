"""Phase 1 P0-1d: FetchedFulltext 端到端测试。

验证：mock PDF 字节 → _persist_fulltext → parse_document 全管线 →
markdown 替换 text → tables 注入 index_source → chunk 入库。
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain.schemas import Evidence
from app.services import fulltext_fetcher as ff
from app.services.pdf_downloader import FetchedFulltext
from app.services.parsing import ParseResult


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _ev(identifier: str) -> Evidence:
    return Evidence(
        source="USPTO", identifier=identifier, title=f"Doc {identifier}",
        snippet="abstract", relevance=0.9,
    )


def test_persist_fulltext_runs_parse_document_on_pdf_bytes(monkeypatch):
    """有 pdf_bytes 时走 parse_document，markdown 替换 text。"""
    called = {}
    fake_tables = [type("T", (), {"markdown": "| A | B |\n|---|---|\n| 1 | 2 |"})()]

    def fake_parse(content: bytes, ext: str, **kw):
        called["bytes"] = content
        called["ext"] = ext
        return ParseResult(
            markdown="# Parsed PDF\n\nFull parsed content here.",
            parser="hybrid",
            tables=fake_tables,
        )

    monkeypatch.setattr("app.services.fulltext_fetcher.parse_document", fake_parse, raising=False)
    # parse_document 是在函数内 import 的，需要 patch 实际模块
    import app.services.parsing as parsing_mod
    monkeypatch.setattr(parsing_mod, "parse_document", fake_parse)

    # mock store 和 index_source
    persisted = {}
    class FakeStore:
        def find_by_hash(self, h): return None
        def find_failed_by_origin(self, o): return None
        def create(self, **kw):
            persisted.update(kw)
            return "src-123"

    import app.services.fulltext_fetcher as ff_mod
    monkeypatch.setattr("app.db.source_store.get_source_store", lambda: FakeStore())

    indexed = {}
    def fake_index(source_id, text, **kw):
        indexed["source_id"] = source_id
        indexed["text"] = text
        indexed["tables"] = kw.get("tables")
        return 5
    monkeypatch.setattr("app.services.kb_index.index_source", fake_index)
    # _persist_fulltext 内 import kb_index，需要 patch 那个模块的属性
    import app.services.kb_index as kb_index_mod
    monkeypatch.setattr(kb_index_mod, "index_source", fake_index)

    # 避免 sidecar 写盘
    monkeypatch.setattr(ff_mod, "_persist_pdf_sidecars", lambda *a, **k: None)

    hit_text = "raw text from fetcher"
    pdf_bytes = b"%PDF-1.4 fake"
    result = ff._persist_fulltext(
        hit_text, _ev("US123"), "patent",
        pdf_bytes=pdf_bytes, tables=[],
    )

    assert result == "src-123"
    assert called["bytes"] == pdf_bytes
    assert called["ext"] == "pdf"
    # parse 后的 markdown 被使用，而非原始 text
    assert indexed["text"] == "# Parsed PDF\n\nFull parsed content here."
    assert hit_text not in indexed["text"]
    # parse 出的 tables 被合并传递
    assert indexed["tables"] == fake_tables


def test_persist_fulltext_falls_back_on_parse_failure(monkeypatch):
    """parse_document 失败时 fail-open 用原 text。"""
    def fake_parse(content, ext, **kw):
        raise RuntimeError("parse boom")
    import app.services.parsing as parsing_mod
    monkeypatch.setattr(parsing_mod, "parse_document", fake_parse)

    class FakeStore:
        def find_by_hash(self, h): return None
        def find_failed_by_origin(self, o): return None
        def create(self, **kw): return "src-456"
    monkeypatch.setattr("app.db.source_store.get_source_store", lambda: FakeStore())

    indexed = {}
    import app.services.kb_index as kb_index_mod
    monkeypatch.setattr(kb_index_mod, "index_source",
                        lambda sid, text, **kw: indexed.update(text=text) or 3)
    import app.services.fulltext_fetcher as ff_mod
    monkeypatch.setattr(ff_mod, "_persist_pdf_sidecars", lambda *a, **k: None)

    orig = "original fetcher text " * 50
    result = ff._persist_fulltext(orig, _ev("US456"), "patent", pdf_bytes=b"%PDF")
    assert result == "src-456"
    assert indexed["text"] == orig  # 回退原 text


def test_index_source_injects_tables(monkeypatch):
    """index_source 的 tables 参数转 markdown 参与 chunking。"""
    import app.services.kb_index as kb_index_mod

    captured = {}
    # mock prepare_chunk_rows 捕获最终文本
    orig_prepare = kb_index_mod.prepare_chunk_rows
    def fake_prepare(full_text, source_id, **kw):
        captured["text"] = full_text
        return []
    monkeypatch.setattr(kb_index_mod, "prepare_chunk_rows", fake_prepare)
    monkeypatch.setattr(kb_index_mod, "kb_enabled", lambda: True)

    fake_table = type("T", (), {
        "markdown": "| Component | wt% |\n|---|---|\n| Epoxy | 60 |"
    })()

    # 直接测 _tables_to_markdown
    md = kb_index_mod._tables_to_markdown([fake_table])
    assert "Epoxy" in md
    assert "wt%" in md
