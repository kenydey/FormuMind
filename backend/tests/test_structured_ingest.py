"""Phase 0: structured-ingest foundation — layout columns, extraction tables,
patent metadata tags.

Covers:
- ``document_chunks`` new columns (bbox / block_type) exist via create_all.
- ``extraction_tables`` / ``extraction_formulas`` tables exist and round-trip.
- ``validate_bbox`` accepts normalized page fractions, rejects the rest.
- ``_classify_block_type`` heuristic on markdown blocks.
- ``extract_patent_tags`` on Chinese + English patent phrasing.
- End-to-end over 10 synthetic patent/SDS-like documents: chunk →
  ChunkStore → read back page_no / block_type / patent tags
  ("which page, which region" reverse lookup).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect

from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.db_common import validate_bbox
from app.db.extraction_store import ExtractionStore
from app.db.models import DocumentChunk, ExtractionFormula, ExtractionTable
from app.services.chunking import _classify_block_type, chunk_markdown
from app.services.metadata_tags import extract_patent_tags


@pytest.fixture()
def factory():
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


@pytest.fixture()
def store(factory):
    return ChunkStore(factory)


@pytest.fixture()
def xstore(factory):
    return ExtractionStore(factory)


# ── schema ──────────────────────────────────────────────────────────────

def test_chunk_layout_columns_exist(factory) -> None:
    cols = {c["name"] for c in inspect(factory().bind).get_columns("document_chunks")}
    assert {"page_no", "bbox", "block_type"} <= cols


def test_extraction_tables_exist(factory) -> None:
    names = set(inspect(factory().bind).get_table_names())
    assert {"extraction_tables", "extraction_formulas"} <= names
    tcols = {c["name"] for c in inspect(factory().bind).get_columns("extraction_tables")}
    assert {"source_id", "page_no", "bbox", "caption", "markdown_text"} <= tcols
    fcols = {c["name"] for c in inspect(factory().bind).get_columns("extraction_formulas")}
    assert {"source_id", "page_no", "bbox", "latex", "formula_no"} <= fcols


# ── validate_bbox ───────────────────────────────────────────────────────

def test_validate_bbox_accepts_normalized() -> None:
    assert validate_bbox([0.1, 0.2, 0.9, 0.8]) == [0.1, 0.2, 0.9, 0.8]
    assert validate_bbox((0, 0, 1, 1)) == [0.0, 0.0, 1.0, 1.0]
    assert validate_bbox(None) is None


def test_validate_bbox_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        validate_bbox([0.1, 0.2, 1.5, 0.8])  # > 1: absolute pixels, not fractions
    with pytest.raises(ValueError):
        validate_bbox([-0.1, 0.2, 0.9, 0.8])


def test_validate_bbox_rejects_malformed() -> None:
    with pytest.raises(ValueError):
        validate_bbox([0.1, 0.2, 0.9])  # not 4 coords
    with pytest.raises(ValueError):
        validate_bbox([0.9, 0.2, 0.1, 0.8])  # min > max
    with pytest.raises(ValueError):
        validate_bbox(["a", "b", "c", "d"])


def test_chunk_store_degrades_bad_bbox_to_none(factory, store) -> None:
    """F-5: a malformed bbox must not sink the whole chunk write — the
    store keeps the chunk and drops only the geometry (validate_bbox itself
    still raises; the stores go through safe_bbox)."""
    sid = str(uuid.uuid4())
    with factory() as session:
        store.replace_for_source_in(
            session, sid, [{"text": "x" * 40, "bbox": [0, 0, 72, 100]}]
        )
        session.commit()
    chunks = store.get_by_source(sid)
    assert len(chunks) == 1
    assert chunks[0].bbox is None


# ── block type heuristic ────────────────────────────────────────────────

def test_classify_block_type() -> None:
    assert _classify_block_type("| A | B |\n|--|--|\n| 1 | 2 |") == "table"
    assert _classify_block_type("<table><tr><td>x</td></tr></table>") == "table"
    assert _classify_block_type("$$E=mc^2$$") == "formula"
    assert _classify_block_type("\\[a+b\\]") == "formula"
    assert _classify_block_type("\\begin{equation}x\\end{equation}") == "formula"
    assert _classify_block_type("```python\nx=1\n```") == "code"
    assert _classify_block_type("![fig](a.png)") == "figure"
    assert _classify_block_type("这是一段普通文本。") == "text"


def test_chunk_markdown_assigns_block_types() -> None:
    md = (
        "<!-- page:3 -->\n\n# 配方\n\n纯文本段落。\n\n"
        "| 组分 | 配比 |\n|---|---|\n| A | 10 |\n\n$$x^2$$\n"
    )
    chunks = chunk_markdown(md)
    by_type = {c.block_type for c in chunks}
    assert {"text", "table", "formula"} <= by_type
    assert all(c.page_no == 3 for c in chunks)


# ── patent metadata tags ────────────────────────────────────────────────

def test_extract_patent_tags_chinese() -> None:
    tags = extract_patent_tags("权利要求 1 所述的组合物，其中……")
    assert tags["claim_no"] == 1
    tags = extract_patent_tags("实施例 3 中，将组分 A 与组分 B 混合……")
    assert tags["example_no"] == 3


def test_extract_patent_tags_english() -> None:
    assert extract_patent_tags("Claim 12. The composition of claim 1 wherein…")["claim_no"] == 12
    assert extract_patent_tags("In Example 2, the mixture was heated…")["example_no"] == 2
    assert extract_patent_tags("embodiment 5 discloses…")["example_no"] == 5


def test_extract_patent_tags_section_title() -> None:
    tags = extract_patent_tags("一些文本", "专利说明书 > 具体实施方式")
    assert tags["section_title"] == "具体实施方式"
    assert extract_patent_tags("plain text") == {}


# ── extraction store round-trip ─────────────────────────────────────────

def test_extraction_store_tables_roundtrip(factory, xstore) -> None:
    sid = str(uuid.uuid4())
    n = xstore.replace_tables(
        sid,
        [
            {
                "page_no": 2,
                "bbox": [0.1, 0.2, 0.8, 0.5],
                "caption": "表1 配方组成",
                "markdown_text": "| A | 10 |",
                "n_rows": 2,
                "n_cols": 2,
            }
        ],
    )
    assert n == 1
    rows = xstore.tables_for_source(sid)
    assert len(rows) == 1
    assert rows[0].page_no == 2
    assert rows[0].bbox == [0.1, 0.2, 0.8, 0.5]
    assert rows[0].caption == "表1 配方组成"
    # Replace semantics: second write wipes the first.
    xstore.replace_tables(sid, [])
    assert xstore.tables_for_source(sid) == []


def test_extraction_store_formulas_roundtrip(factory, xstore) -> None:
    sid = str(uuid.uuid4())
    xstore.replace_formulas(
        sid,
        [{"page_no": 4, "bbox": [0.2, 0.3, 0.7, 0.35], "latex": "E=mc^2", "formula_no": "(3)"}],
    )
    rows = xstore.formulas_for_source(sid)
    assert len(rows) == 1
    assert rows[0].latex == "E=mc^2"
    assert rows[0].formula_no == "(3)"


def test_extraction_store_degrades_bad_bbox_to_none(factory, xstore) -> None:
    """F-5: bad geometry is dropped (None), the table row is still stored."""
    sid = str(uuid.uuid4())
    xstore.replace_tables(sid, [{"markdown_text": "| a |", "bbox": [0, 0, 2, 1]}])
    rows = xstore.tables_for_source(sid)
    assert len(rows) == 1
    assert rows[0].bbox is None


# ── end-to-end: 10 synthetic documents ────────────────────────────────────

def _synthetic_doc(i: int) -> str:
    """Patent/SDS-like markdown with page markers, a table and claim language."""
    return (
        f"<!-- page:{i + 1} -->\n\n# 测试文档 {i}\n\n"
        f"权利要求 {i + 1} 所述的组合物，包含组分 A。\n\n"
        f"| 组分 | 含量 |\n|---|---|\n| A | {10 + i} |\n\n"
        f"<!-- page:{i + 2} -->\n\n## 具体实施方式\n\n"
        f"实施例 {i + 1} 中，按表配制并测试性能。\n"
    )


def test_ten_docs_reverse_lookup(factory, store) -> None:
    """Every chunk of 10 docs must be traceable to its page and patent tags."""
    sids = []
    for i in range(10):
        sid = str(uuid.uuid4())
        sids.append(sid)
        chunks = chunk_markdown(_synthetic_doc(i))
        rows = []
        for c in chunks:
            tags = extract_patent_tags(c.text, c.heading_path)
            rows.append(
                {
                    "text": c.text,
                    "heading_path": c.heading_path,
                    "page_no": c.page_no,
                    "paragraph_idx": c.paragraph_idx,
                    "offset_start": c.offset_start,
                    "offset_end": c.offset_end,
                    "bbox": None,  # text chain: no geometry yet (Phase 1 fills)
                    "block_type": c.block_type,
                    "meta": {"patent_tags": tags} if tags else None,
                }
            )
        with factory() as session:
            store.replace_for_source_in(session, sid, rows)
            session.commit()

    with factory() as session:
        all_rows = (
            session.query(DocumentChunk)
            .filter(DocumentChunk.source_id.in_(sids))
            .all()
        )
    assert len(all_rows) > 0
    for row in all_rows:
        # Page provenance survives for every chunk.
        assert row.page_no is not None and row.page_no >= 1
        # Block types are populated (text/table at least across the corpus).
        assert row.block_type in {"text", "table", "formula", "figure", "code"}
        # bbox stays NULL on the text chain — explicit, not accidental.
        assert row.bbox is None

    with factory() as session:
        claim_rows = (
            session.query(DocumentChunk)
            .filter(DocumentChunk.source_id.in_(sids))
            .all()
        )
    tagged = [
        r for r in claim_rows if (r.meta or {}).get("patent_tags", {}).get("claim_no")
    ]
    exampled = [
        r for r in claim_rows if (r.meta or {}).get("patent_tags", {}).get("example_no")
    ]
    titled = [
        r
        for r in claim_rows
        if (r.meta or {}).get("patent_tags", {}).get("section_title") == "具体实施方式"
    ]
    # One claim chunk and one example chunk per synthetic doc.
    assert len(tagged) == 10, f"expected 10 claim-tagged chunks, got {len(tagged)}"
    assert len(exampled) == 10, f"expected 10 example-tagged chunks, got {len(exampled)}"
    assert len(titled) == 10
    # Reverse lookup: "page 5 of doc 5, table region" is answerable.
    with factory() as session:
        page2 = (
            session.query(DocumentChunk)
            .filter(
                DocumentChunk.source_id == sids[4],
                DocumentChunk.page_no == 5,
                DocumentChunk.block_type == "table",
            )
            .all()
        )
    assert len(page2) >= 1
