"""The KB quality gate must not throw short formulas away (round-5; open item 7 of the round-4 plan).

``is_garbage_chunk_text`` drops a chunk when fewer than 40 % of its characters are letters / digits / CJK. A formula is
mostly operators by construction - ``$$k = A e^{-E_a / (RT)}$$`` scores 0.27 - and ``chunking`` emits it as its own
atomic chunk, so there is no prose beside it to lift the ratio. Measured on what real documents contain: long formulas
(0.55-0.58), code (0.54), captions (0.58) and image links (0.70) pass; short ones did not. A formula is now measured by
length (the gate's own floor) plus a few word characters, not by density.
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.services import kb_index
from app.services.kb_retrieval_gate import (
    chunk_floor,
    drop_reason_for_chunk,
    gate_ingest_rows,
    is_formula_text,
    is_garbage_chunk_text,
)

ARRHENIUS = r"$$k = A e^{-E_a / (RT)}$$"
STRESS = r"$$\sigma = \frac{F}{A}$$"
LONG_FORMULA = (
    r"$$\Delta G = \Delta H - T \Delta S = -RT \ln K_{eq} + \sum_i \nu_i \mu_i^{\circ}$$"
)
BLOCK = "\\begin{equation}\nR_p = \\frac{d E}{d i}\n\\end{equation}"


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setenv("FORMUMIND_CONTENT_FILTER_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize("formula", [ARRHENIUS, STRESS, LONG_FORMULA, BLOCK], ids=["arrhenius", "stress", "long", "equation-env"])
def test_a_formula_is_not_garbage_whatever_its_operator_density(formula):
    assert len(formula) >= chunk_floor()
    assert not is_garbage_chunk_text(formula, block_type="formula")
    assert not is_garbage_chunk_text(formula), "a chunk that opens like a formula counts as one even without a block_type"


def test_the_arrhenius_chunk_really_was_below_the_density_rule():
    """Not vacuous: the same characters, filed as text, are still judged by density - which is what dropped it."""
    assert is_garbage_chunk_text(ARRHENIUS, block_type="text")


@pytest.mark.parametrize(
    "junk",
    [
        "$$x = 1$$",  # below the length floor
        "$$" + "-" * 30 + "$$",  # long enough, nothing in it
        "$$" + "\\\\" * 15 + "$$",
        "$$ $$",
        "$$" + "=" * 24 + "$$",
    ],
)
def test_a_formula_still_has_to_contain_something(junk):
    assert is_garbage_chunk_text(junk, block_type="formula")
    assert is_garbage_chunk_text(junk)


def test_other_blocks_are_scored_exactly_as_before():
    assert not is_garbage_chunk_text("环氧树脂作为主要成膜物质，具有优异的附着力和耐化学性。")
    assert is_garbage_chunk_text("a - - - - - - - - - - - - - - - - - - - - - - - - - - - - - b")
    assert is_garbage_chunk_text("!!!@@@###$$$%%%^^^&&&***((()))", block_type="text")
    # the exemption is for formulas only: symbol noise filed as a table / code / figure is still noise
    for kind in ("table", "code", "figure"):
        assert is_garbage_chunk_text("-----------------------------------------", block_type=kind)


def test_the_block_type_beats_the_guess_from_the_text():
    assert is_formula_text("plain words", "formula")
    assert not is_formula_text(ARRHENIUS, "text"), "an explicit block_type is believed"
    assert is_formula_text(ARRHENIUS) and is_formula_text("  \n" + BLOCK)
    assert not is_formula_text("The rate follows $$k = A e^{-E_a/RT}$$ as written.")


class _Chunk:
    def __init__(self, text, block_type=None, source_id=""):
        self.text, self.block_type, self.source_id = text, block_type, source_id


def test_retrieval_keeps_a_stored_formula_chunk():
    assert drop_reason_for_chunk(_Chunk(ARRHENIUS, "formula")) is None
    assert drop_reason_for_chunk(_Chunk(ARRHENIUS, "text")) == "garbage_snippet"


def test_ingest_keeps_a_formula_row_and_still_drops_noise():
    rows = [
        {"text": ARRHENIUS, "block_type": "formula"},
        {"text": "$$x = 1$$", "block_type": "formula"},
        {"text": "$$" + "-" * 30 + "$$", "block_type": "formula"},
        {"text": "环氧树脂作为主要成膜物质，具有优异的附着力和耐化学性。", "block_type": "text"},
    ]
    kept, reason = gate_ingest_rows(rows, source_id=None)
    assert [r["text"] for r in kept] == [ARRHENIUS, rows[3]["text"]]
    assert reason is None


def test_end_to_end_a_short_formula_between_paragraphs_reaches_the_rows():
    """Before: dropped twice - by the 30-character pre-filter, then by the density rule."""
    document = (
        "# 腐蚀动力学\n\n"
        "温度对腐蚀速率的影响通常用阿伦尼乌斯方程描述，速率常数随温度升高而指数增大，"
        "因此高温盐雾试验可以作为加速试验使用。\n\n"
        f"{ARRHENIUS}\n\n"
        "式中 A 为指前因子，Ea 为活化能，R 为气体常数，T 为热力学温度，"
        "在 25 到 80 摄氏度范围内该关系对环氧体系成立。\n"
    )
    rows = kb_index.prepare_chunk_rows(document, "src-formula", embed=False, gate_fn=lambda rows: gate_ingest_rows(rows))
    assert rows is not None
    formulas = [r for r in rows if r["block_type"] == "formula"]
    assert [r["text"].strip() for r in formulas] == [ARRHENIUS]
    assert len(rows) == 3


def test_end_to_end_a_trivial_formula_is_still_not_indexed():
    document = "# 记号\n\n" + "说明文字" * 20 + "\n\n$$x = 1$$\n\n" + "后续说明" * 20 + "\n"
    rows = kb_index.prepare_chunk_rows(document, "src-trivial", embed=False, gate_fn=lambda rows: gate_ingest_rows(rows))
    assert rows is not None
    assert not [r for r in rows if r["block_type"] == "formula"]
