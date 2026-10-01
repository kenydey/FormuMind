"""MinerU table golden tests (C-2, no real sampling yet).

Scope honesty: the content_list fixtures below are STRUCTURAL SIMULATIONS
built from the known field set (type/page_idx/text/text_level/table_body/
caption/img_path) — NOT real MinerU API output. They verify the parsing
plumbing (bbox pass-through, n_rows/n_cols from HTML). Replace with real
sampled content_list when the 20-table sampling lands.

Covers:
1. bbox pass-through: present -> kept, absent -> None, malformed -> None;
2. _html_table_shape: row/col counts from table HTML (local, no MinerU);
3. persist_structured writes bbox + n_rows/n_cols to the extraction tables.
"""
from __future__ import annotations

import pytest

from app.services import mineru_cloud, mineru_structured
from app.services.mineru_structured import (
    MinerUStructured,
    StructuredBlock,
    _html_table_shape,
    _normalise_blocks,
)


def _raw_table(**kw):
    base = {
        "type": "table",
        "page_idx": 2,
        "text": "",
        "text_level": 0,
        "table_body": (
            "<table><tr><th>组分</th><th>含量%</th></tr>"
            "<tr><td>环氧树脂</td><td>45.0</td></tr>"
            "<tr><td>固化剂</td><td>15.0</td></tr></table>"
        ),
        "table_caption": "表1 配方组成",
        "img_path": "",
    }
    base.update(kw)
    return base


# ── 1. bbox pass-through at the cloud normalise layer ─────────────────────


def _doc_of(*raws):
    from types import SimpleNamespace

    return SimpleNamespace(content_list=list(raws), markdown="")


def test_normalise_passes_bbox_when_present():
    doc = mineru_cloud._normalise(
        _doc_of(_raw_table(bbox=[10.0, 20.0, 100.0, 200.0])), {}
    )
    assert doc.blocks[0].bbox == [10.0, 20.0, 100.0, 200.0]


def test_normalise_bbox_none_when_absent():
    doc = mineru_cloud._normalise(_doc_of(_raw_table()), {})
    assert doc.blocks[0].bbox is None


@pytest.mark.parametrize("bad", [[1.0, 2.0], [1, 2, 3, 4, 5], "nope", None])
def test_normalise_bbox_malformed_becomes_none(bad):
    raw = _raw_table()
    if bad is not None:
        raw["bbox"] = bad
    doc = mineru_cloud._normalise(_doc_of(raw), {})
    assert doc.blocks[0].bbox is None


def test_structured_blocks_carry_bbox():
    cloud_doc = mineru_cloud.MinerUDocument(
        blocks=[
            mineru_cloud.MinerUBlock(
                type="table", page_idx=0, html="<table></table>", bbox=[1, 2, 3, 4]
            )
        ]
    )
    blocks = _normalise_blocks(cloud_doc)
    assert blocks[0].bbox == [1, 2, 3, 4]


# ── 2. n_rows / n_cols from HTML (local) ───────────────────────────────────


@pytest.mark.parametrize(
    "html,expected",
    [
        # header + 2 data rows, 2 cols
        (
            "<table><tr><th>a</th><th>b</th></tr>"
            "<tr><td>1</td><td>2</td></tr>"
            "<tr><td>3</td><td>4</td></tr></table>",
            (3, 2),
        ),
        # ragged rows: cols = max
        (
            "<table><tr><td>1</td></tr><tr><td>2</td><td>3</td><td>4</td></tr></table>",
            (2, 3),
        ),
        # empty / no rows
        ("", (None, None)),
        ("<table></table>", (None, None)),
        ("<p>not a table</p>", (None, None)),
    ],
)
def test_html_table_shape(html, expected):
    assert _html_table_shape(html) == expected


# ── 3. persist writes bbox + shape ────────────────────────────────────────


def test_persist_structured_writes_bbox_and_shape(monkeypatch):
    captured: dict = {}

    class _FakeStore:
        def __init__(self, *a, **k):
            pass

        def replace_tables(self, source_id, tables):
            captured["source_id"] = source_id
            captured["tables"] = tables

        def replace_formulas(self, source_id, formulas):
            captured["formulas"] = formulas

    import app.services.mineru_structured as ms_mod
    import app.db.extraction_store as es_mod

    monkeypatch.setattr(es_mod, "ExtractionStore", _FakeStore)
    monkeypatch.setattr(
        "app.db.database.default_session_factory", lambda: object()
    )

    structured = MinerUStructured(
        markdown="",
        blocks=[
            StructuredBlock(
                page_no=3,
                kind="table",
                html=_raw_table()["table_body"],
                caption="表1 配方组成",
                bbox=[10.0, 20.0, 100.0, 200.0],
            )
        ],
    )
    mineru_structured.persist_structured("src-1", structured)
    (t,) = captured["tables"]
    assert t["page_no"] == 3
    assert t["bbox"] == [10.0, 20.0, 100.0, 200.0]
    assert t["n_rows"] == 3
    assert t["n_cols"] == 2
    assert "环氧树脂" in t["markdown_text"]
