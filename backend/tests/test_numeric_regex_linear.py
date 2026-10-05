"""A digit run must not be a CPU trap for the numeric gate (round-4 sweep finding).

``extract_numbers`` (every LLM answer and every evidence text goes through it) and the agent loop's numeric facets
matched ``(\\d+(?:\\.\\d+)?)\\s*(unit|unit|…)`` from every position of a long digit run — each attempt backing off one digit at
a time through the whole unit alternation. 4,000 digits with no unit took 3.5 s and the cost grew with the square.
Starting only at the beginning of a run (``(?<!\\d)``) gives the same matches in linear time.
"""
from __future__ import annotations

import random
import re
import time

import pytest

from app.services import agent_search_loop as agent
from app.services import moljson, numeric_check as nc


def _seconds(fn, *args) -> float:
    start = time.perf_counter()
    fn(*args)
    return time.perf_counter() - start


# Explicit ids: a parameter this long becomes part of the node id, and pytest keeps the node id in the environment
# variable PYTEST_CURRENT_TEST - which Windows caps at 32,767 characters (these were setup errors there).
@pytest.mark.parametrize(
    "text",
    [
        pytest.param("1" * 40_000, id="digits-40k"),
        pytest.param("1" * 20_000 + "-" + "1" * 20_000, id="digits-dash-digits-40k"),
        pytest.param("1." * 20_000, id="dotted-digits-40k"),
        pytest.param("9" * 30_000 + " m", id="digits-then-unit-30k"),
    ],
)
def test_long_digit_runs_are_extracted_in_bounded_time(text):
    assert _seconds(nc.extract_numbers, text) < 3.0
    assert _seconds(agent.extract_numeric_facets, text) < 3.0


@pytest.mark.parametrize(
    "text, expected",
    [
        ("膜厚 80 μm，盐雾 720 h", [(80.0, "um"), (720.0, "h")]),
        ("pH 8.5", [(8.5, "ph")]),
        ("pH 控制在 3.8-4.2", [(3.8, "ph"), (4.2, "ph")]),
        ("x12.5mg", [(12.5, "mg")]),
        ("3.5.2 mg", [(5.2, "mg")]),  # unchanged: the version-looking prefix does not hide the number with a unit
        ("12345 mg", [(12345.0, "mg")]),
        ("20-30 mg/L", [(20.0, "mg"), (30.0, "mg")]),
    ],
)
def test_ordinary_extraction_is_unchanged(text, expected):
    assert [(round(v, 6), u) for v, u in nc.extract_numbers(text)] == expected


def test_the_start_anchor_does_not_change_what_matches():
    """Differential: the pattern with the anchor finds exactly what the pattern without it found."""
    old_num = re.compile(r"(\d+(?:\.\d+)?)\s*(" + nc._UNIT_PATTERN + ")", re.IGNORECASE)
    old_range = re.compile(
        r"(\d+(?:\.\d+)?)\s*[-–—~〜]\s*(\d+(?:\.\d+)?)\s*(" + nc._UNIT_PATTERN + ")", re.IGNORECASE
    )
    rng = random.Random(11)
    alphabet = ["1", "2", "0", ".", " ", "-", "~", "mg", "g", "h", "%", "pH", "x", "µm", "mL", "°C", "wt%"]
    for _ in range(30_000):
        s = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 14)))
        assert [m.groups() for m in nc._NUM_RE.finditer(s)] == [m.groups() for m in old_num.finditer(s)], repr(s)
        assert [m.groups() for m in nc._RANGE_RE.finditer(s)] == [m.groups() for m in old_range.finditer(s)], repr(s)


def test_numeric_facets_still_find_numbers_with_units():
    assert agent.extract_numeric_facets("耐蚀性 > 72h 且膜厚 80 μm") == ["72h", "80μm"]


def test_an_absurdly_long_smiles_is_refused_not_parsed():
    if not moljson.rdkit_available():
        pytest.skip("RDKit not installed")
    start = time.perf_counter()
    result = moljson.validate_smiles("C" * (moljson.MAX_SMILES_CHARS + 1))
    assert result["valid"] is False and time.perf_counter() - start < 1.0
    assert moljson.validate_smiles("CCO")["valid"] is True


# ── the same number written with a different but equivalent character ────────────


@pytest.mark.parametrize(
    "written, facet",
    [
        ("膜厚 80 \u03bcm", "80\u03bcm"),  # GREEK SMALL LETTER MU — the only spelling the alias table listed
        ("膜厚 80 \u00b5m", "80\u03bcm"),  # MICRO SIGN — what Word and most keyboards produce
        ("膜厚 \uff18\uff10 \u00b5m", "80\u03bcm"),  # full-width digits
        ("膜厚 80 um", "80um"),
    ],
)
def test_every_spelling_of_micrometres_is_the_same_number(written, facet):
    assert nc.extract_numbers(written) == [(80.0, "um")]
    assert agent.extract_numeric_facets(written) == [facet]


def test_full_width_percent_and_compatibility_units_are_found():
    assert nc.extract_numbers("固体含量 65\uff05") == [(65.0, "pct")]
    assert nc.extract_numbers("厚度 10\u339c") == [(10.0, "mm")]  # the single character ㎜


def test_a_question_and_its_evidence_agree_on_the_micro_sign():
    from app.domain.schemas import Evidence

    question = agent.extract_numeric_facets("膜厚 80 \u03bcm 是多少")
    evidence = Evidence(source="t", identifier="d", title="TDS", snippet="干膜厚度 80 \u00b5m", relevance=0.5)
    assert agent.assess_gap(question, [evidence]) == []
