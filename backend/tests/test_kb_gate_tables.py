"""The KB quality gate must not throw tables away (round-4).

``is_garbage_chunk_text`` drops a chunk when fewer than 40 % of its characters are letters / digits / CJK.
A pipe table is mostly punctuation by construction — ``| 固体含量 | 65 | % |``, the ``| --- | --- |`` separator
row, the padding around every cell — and ``chunking`` emits every table as its own atomic chunk, so there is
no prose beside it to lift the ratio. The result, end to end, for a one-page datasheet: ``kb ingest gate:
source … all 1 chunk(s) garbage — writing 0``. The table — the one part of a TDS that answers "粘度是多少" —
never reached the knowledge base, and the same rule removed it from retrieval results when it had.

Measured on what the converters emit: MarkItDown (.docx / .xlsx / .pptx) pads cells (ratio 0.31–0.34), pymupdf4llm
bolds headers and breaks wrapped cells with ``<br>`` (0.38).
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.services import kb_index
from app.services.kb_retrieval_gate import is_garbage_chunk_text

MARKITDOWN_TABLE = """|  |  |  |
| --- | --- | --- |
| 项目 | 指标 | 单位 |
| 固体含量 | 65 | % |
| 粘度 | 1200 | mPa·s |
| 密度 | 1.35 | g/cm3 |
| 耐盐雾性能 | 720 | h |"""

PYMUPDF_TABLE = """|**Sample**|**Epoxy<br>(wt%)**|**Zn phosphate<br>(wt%)**|**Salt spray (h)**|
|---|---|---|---|
|A|62|0|480|
|B|55|8|720|
|C|50|12|690|"""

WIDE_NUMERIC = """| t/h | 0 | 24 | 48 | 72 |
| --- | --- | --- | --- | --- |
| A | 1.0 | 0.9 | 0.8 | 0.7 |
| B | 1.0 | 0.95 | 0.9 | 0.85 |"""

HTML_TABLE = (
    "<table><tr><th>项目</th><th>指标</th><th>单位</th></tr>"
    "<tr><td>固体含量</td><td>65</td><td>%</td></tr>"
    "<tr><td>粘度</td><td>1200</td><td>mPa·s</td></tr></table>"
)


@pytest.mark.parametrize(
    "table", [MARKITDOWN_TABLE, PYMUPDF_TABLE, WIDE_NUMERIC, HTML_TABLE],
    ids=["markitdown-padded", "pymupdf-bold-br", "wide-numeric", "html"],
)
def test_a_table_with_content_is_not_garbage(table):
    assert not is_garbage_chunk_text(table)


@pytest.mark.parametrize(
    "junk",
    [
        "|   |   |\n| --- | --- |",
        "| | | | |",
        "|---|---|",
        "|.|.|.|.|.|.|.|.|.|.|.|",
        "<table><tr><td></td></tr></table>",
        "-----------------------------------------",
        "!!!@@@###$$$%%%^^^&&&***((()))",
    ],
)
def test_markup_without_content_is_still_garbage(junk):
    assert is_garbage_chunk_text(junk)


def test_text_is_scored_exactly_as_before():
    assert not is_garbage_chunk_text("环氧树脂作为主要成膜物质，具有优异的附着力和耐化学性。")
    assert is_garbage_chunk_text("表1 典型性能")  # below the length floor
    assert is_garbage_chunk_text("a - - - - - - - - - - - - - - - - - - - - - - - - - - - - - b")


def test_a_pipe_in_running_text_does_not_hide_symbol_noise():
    assert is_garbage_chunk_text("| ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ |")


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_store_mod
    import app.db.database as db_mod
    import app.db.source_store as source_store_mod
    from app.db.chunk_store import ChunkStore
    from app.db.source_store import SourceStore

    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_CONTENT_FILTER_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_KB_V2_ENABLED", "true")
    get_settings.cache_clear()
    engine = make_engine(f"sqlite:///{tmp_path}/kb_gate_tables.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src, chk = SourceStore(factory), ChunkStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)
    yield src, chk
    get_settings.cache_clear()


def test_a_datasheet_table_reaches_the_knowledge_base(stores):
    """The page from the report: heading, caption and one table — the table used to be the part that vanished."""
    src, chk = stores
    page = f"# 环氧锌磷酸盐底漆 技术数据表（TDS）\n\n表1 典型性能\n\n{MARKITDOWN_TABLE}\n"
    sid = src.create(
        filename="tds.docx", title="TDS", source_kind="local", full_text=page, content_hash="tds-table"
    )
    assert kb_index.index_source(sid, page, embed=False) >= 1
    stored = chk.get_by_source(sid)
    assert any("耐盐雾性能" in c.text and "720" in c.text for c in stored), [c.text[:40] for c in stored]
    assert any(c.block_type == "table" for c in stored)


def test_numeric_exemption_case_insensitive_units():
    """v27 P2-13: 大写单位也拿数字豁免（floor 8 而非 20）。"""
    assert not is_garbage_chunk_text("含量 100 MG")   # 9 chars ≥ 8
    assert not is_garbage_chunk_text("10 KG 含量x")
    assert not is_garbage_chunk_text("8 H 测试xx")    # 8 chars ≥ 8
    assert not is_garbage_chunk_text("20 MPA 盐雾")
    assert not is_garbage_chunk_text("10 L 溶剂xx")   # 大写 L 仍豁免


def test_numeric_exemption_no_word_internal_match():
    """v27 P2-13: admin/things/panel 等词内匹配不豁免；L 保持大小写敏感。"""
    assert is_garbage_chunk_text("admin panel v2")  # panel 词尾 l 不命中（L 大小写敏感）
    assert is_garbage_chunk_text("things 12345")    # 无单位 → floor 20
    assert is_garbage_chunk_text("10 l 溶剂xx")      # 小写 l 不豁免
