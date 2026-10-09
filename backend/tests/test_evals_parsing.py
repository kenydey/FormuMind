"""The parsing suite (``backend/evals/suites/parsing.py``): its scoring is checked on outputs written by hand, its documents are
checked to be real files, and what the evaluation found and the application fixed is pinned on the production parsers.
"""
from __future__ import annotations

import importlib
import importlib.util
import zipfile
from io import BytesIO

import pytest
from app.services import pdf_local
from evals import parsing_cases as PC
from evals.suites import parsing as P

TABLE = PC.Truth(
    cells=("Sample", "Zn (wt%)", "S01", "4.5", "S02", "5.0"),
    rows=(("Sample", "Zn (wt%)"), ("S01", "4.5"), ("S02", "5.0")),
    numbers=("4.5", "5.0"),
)
PIPES = "| Sample | Zn (wt%) |\n| --- | --- |\n| S01 | 4.5 |\n| S02 | 5.0 |"


# ── scoring, on outputs written by hand ───────────────────────────────────────────────────────────


def test_a_faithful_table_scores_one_on_every_metric_it_defines():
    scores = P.score_output(PIPES, TABLE)
    assert scores["cell_recall"] == scores["row_integrity"] == scores["number_fidelity"] == 1.0
    assert scores["order"] is None and scores["unit_fidelity"] is None and scores["noise_free"] is None
    assert P.case_score(scores) == 1.0


def test_a_table_flattened_to_one_cell_per_line_keeps_its_text_and_loses_its_rows():
    scores = P.score_output("Sample\nZn (wt%)\nS01\n4.5\nS02\n5.0", TABLE)
    assert scores["cell_recall"] == 1.0 and scores["row_integrity"] == 0.0


def test_a_dropped_table_scores_zero():
    scores = P.score_output("Table 1. Primer formulation", TABLE)
    assert scores["cell_recall"] == scores["row_integrity"] == scores["number_fidelity"] == 0.0 and P.case_score(scores) == 0.0


def test_a_figure_must_be_a_whole_token():
    assert P.has_fragment("| 14.5 |", "4.5") is False
    assert P.has_fragment("| 35.05 |", "35.0") is False
    assert P.has_fragment("| 4.5 |", "4.5") and P.has_fragment("value: 4.5.", "4.5") and P.has_fragment("4.5%", "4.5")
    assert P.has_fragment("grade 0 film", "0") and not P.has_fragment("grade 10 film", "0")


def test_reading_order_drops_when_columns_are_read_straight_across():
    truth = PC.Truth(order=("left one", "left two", "right one", "right two"))
    assert P.score_output("left one left two right one right two", truth)["order"] == 1.0
    assert P.score_output("left one right one left two right two", truth)["order"] == 0.75


def test_a_unit_may_lose_its_blank_but_not_its_digits():
    truth = PC.Truth(units=("50 µm", "120 °C", "≥ 500 h"))
    assert P.score_output("film 50µm, cure 120 °C, salt spray ≥500 h", truth)["unit_fidelity"] == 1.0
    assert P.score_output("film 5 µm, cure 120 °C, salt spray ≥ 500 h", truth)["unit_fidelity"] == pytest.approx(2 / 3)


def test_what_must_be_absent_and_what_may_appear_once():
    truth = PC.Truth(absent=("Unnamed", "NaN"), repeats=("Technical Note",))
    assert P.score_output("a | b\nTechnical Note\nbody", truth)["noise_free"] == 1.0
    assert P.score_output("Unnamed: 1 | NaN\nTechnical Note\nTechnical Note", truth)["noise_free"] == 0.0
    assert P.score_output("A NaN cell", truth)["noise_free"] == pytest.approx(2 / 3)  # one absent word found
    assert P.score_output("Finance and banana", truth)["noise_free"] == 1.0  # "nan" inside a word is not the word


def test_a_case_is_scored_on_the_metrics_its_truth_defines_only():
    assert P.case_score({"cell_recall": 1.0, "row_integrity": 0.5, "order": None, "number_fidelity": None,
                         "unit_fidelity": None, "noise_free": None}) == 0.75
    assert P.case_score({name: None for name in P.METRICS}) == 0.0


# ── the documents are real files ──────────────────────────────────────────────────────────────────


def _available(case: PC.Case) -> bool:
    try:
        for module in case.needs:
            importlib.import_module(module)
    except ImportError:
        return False
    return True


CASES = [c for c in PC.cases() if _available(c)]


def test_the_set_covers_the_formats_and_kinds_it_claims():
    cases = PC.cases()
    assert {c.ext for c in cases} == {"docx", "xlsx", "pdf", "html"}
    assert {c.kind for c in cases} == {"table", "reading_order", "units", "noise"}
    assert len({c.id for c in cases}) == len(cases)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_every_case_builds_a_real_file_that_holds_its_own_truth(case):
    content = case.build()
    assert content
    if case.ext in ("docx", "xlsx"):
        assert zipfile.ZipFile(BytesIO(content)).testzip() is None
    elif case.ext == "pdf":
        assert content.startswith(b"%PDF")
    else:
        assert b"<table" in content
    truth = case.truth
    assert truth.cells or truth.order or truth.units or truth.rows, "a case with nothing to check scores nothing"
    if case.ext == "html":
        assert case.build() == content  # the others carry a creation timestamp


def test_a_case_whose_generator_is_missing_is_skipped_with_the_reason_not_failed(monkeypatch):
    real = importlib.import_module

    def without_fpdf(name, *args, **kwargs):
        if name == "fpdf":
            raise ImportError("no fpdf here")
        return real(name, *args, **kwargs)

    monkeypatch.setattr(P.importlib, "import_module", without_fpdf)
    result = P.run(systems={"production": P.naive_parser})
    skipped = {s["id"]: s["reason"] for s in result["skipped"]}
    needs_fpdf = {case.id for case in PC.cases() if "fpdf" in case.needs}
    assert needs_fpdf and needs_fpdf <= set(skipped)
    assert all("fpdf" in skipped[case_id] for case_id in needs_fpdf)
    assert all(c["ext"] != "pdf" for c in result["cases"])  # none of them was run, scored or reported as a failure


# ── the production parsers, as the evaluation found them and the application fixed them ───────────


@pytest.fixture(scope="module")
def result():
    return P.run()


def _case(result, case_id):
    return next(c for c in result["cases"] if c["id"] == case_id)


def _have(*modules: str) -> bool:
    return all(importlib.util.find_spec(m) is not None for m in modules)


@pytest.mark.skipif(not _have("markitdown", "docx"), reason="the blocking CI job has no MarkItDown: Word tables go through python-docx there")
def test_word_tables_survive_where_the_last_resort_tier_loses_them_all(result):
    # v28 P-1 起：python-docx fallback tier 也保留表格（不再静默丢弃），
    # 因此 naive 分数不再 < 0.5。核心断言：production 满分，且不低于 naive。
    for case_id in ("docx-table", "docx-merged-header", "docx-reading-order"):
        assert _case(result, case_id)["production"]["score"] == 1.0, case_id
        assert _case(result, case_id)["production"]["score"] >= _case(result, case_id)["naive"]["score"], case_id


def test_a_spreadsheet_with_a_title_row_comes_out_clean(result):
    pytest.importorskip("openpyxl")
    case = _case(result, "xlsx-title-row")
    assert case["production"]["parser"] == "openpyxl" and case["production"]["score"] == 1.0


@pytest.mark.skipif(not (pdf_local.local_available()[0] and _have("fpdf")), reason="needs pymupdf4llm and fpdf2")
def test_pdf_pages_come_out_whole_whichever_pdf_was_parsed_before(result):
    """The layout-cache bug: after a one-page PDF the next PDF's first page was blank or out of order."""
    for case_id in ("pdf-two-column-ordered", "pdf-two-column-interleaved", "pdf-units", "pdf-table-multipage", "pdf-table-borderless"):
        assert _case(result, case_id)["production"]["score"] == 1.0, case_id
    assert _case(result, "pdf-running-header")["production"]["metrics"]["cell_recall"] == 1.0


def test_production_is_never_below_the_last_resort_tier_by_much(result):
    for case in result["cases"]:
        assert case["production"]["score"] >= case["naive"]["score"] - 0.15, case["id"]


@pytest.mark.skipif(
    not (pdf_local.local_available()[0] and _have("markitdown", "docx", "fpdf", "openpyxl", "trafilatura")),
    reason="the whole parser stack is needed for the overall figure",
)
def test_with_the_whole_stack_installed_the_overall_score_holds(result):
    systems = result["systems"]
    assert systems["production"]["overall"]["mean_score"] >= 0.9
    # v28 P-1 起：naive tier（python-docx fallback）也保留表格，分数从
    # 低位升至 ~0.92，与 production 的差距收窄。保留"production 不劣于
    # naive"的核心断言，阈值从 +0.15 放宽。
    assert systems["production"]["overall"]["mean_score"] >= systems["naive"]["overall"]["mean_score"]
