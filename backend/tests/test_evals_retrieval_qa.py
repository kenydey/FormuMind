"""The retrieval and QA suites (``backend/evals/suites``): the harness must tell a good retriever from a bad one, and the
application's own retriever must stay where the evaluation found it.

The first group feeds the suites retrievers whose quality is known by construction (perfect, empty, random) and checks the
numbers come out as arithmetic says. The second runs the production retriever over the 52-document corpus (a few seconds) and
pins the results that matter with margin - not the exact figures, which belong to ``evals/baselines/baseline.json``.
"""
from __future__ import annotations

import pytest
from evals import metrics as M
from evals.datasets import load_corpus, load_queries
from evals.suites import qa, retrieval
from evals.suites.retrieval import Hit, collect, evaluate

DOCS = load_corpus()
QUERIES = load_queries()
TEXT = {d.id: d.text for d in DOCS}


def _perfect(query: str, k: int) -> list[Hit]:
    q = next(x for x in QUERIES if x.text == query)
    order = sorted(q.relevant, key=lambda d: -q.relevant[d])
    return [Hit(d, 0.9 - 0.01 * i, TEXT[d]) for i, d in enumerate(order[:k])]


def _empty(query: str, k: int) -> list[Hit]:
    return []


def _everything_but_the_answer(query: str, k: int) -> list[Hit]:
    q = next(x for x in QUERIES if x.text == query)
    wrong = [d.id for d in DOCS if d.id not in q.relevant]
    return [Hit(d, 0.5, TEXT[d]) for d in wrong[:k]]


def _evaluate(retriever) -> dict:
    return evaluate(collect(production=retriever, use_production=False))


# ── the harness discriminates ─────────────────────────────────────────────────────────────────────


def test_a_perfect_retriever_scores_one_everywhere_and_a_blind_one_scores_zero():
    perfect = _evaluate(_perfect)["systems"]["production"]["overall"]
    assert all(perfect[m]["mean"] == pytest.approx(1.0) for m in ("success@1", "success@5", "recall@5", "mrr@10", "ndcg@10"))
    blind = _evaluate(_everything_but_the_answer)["systems"]["production"]["overall"]
    assert all(blind[m]["mean"] == 0.0 for m in ("success@1", "success@5", "mrr@10", "ndcg@10"))


def test_the_reference_bm25_is_far_above_a_coin_toss_and_the_toss_is_near_the_floor():
    systems = _evaluate(_perfect)["systems"]
    assert systems["bm25_reference"]["overall"]["success@5"]["mean"] > systems["random"]["overall"]["success@5"]["mean"] + 0.6
    assert systems["random"]["overall"]["success@1"]["mean"] < 0.1


def test_abstention_is_separable_only_when_confidence_differs_between_answerable_and_not():
    def confident_when_it_knows(query: str, k: int) -> list[Hit]:
        hits = _perfect(query, k)
        return hits or [Hit(DOCS[0].id, 0.1, DOCS[0].text)]

    assert _evaluate(confident_when_it_knows)["abstention"]["production"]["auroc"] == 1.0

    def constant(query: str, k: int) -> list[Hit]:
        return [Hit(DOCS[0].id, 0.3, DOCS[0].text)]

    assert _evaluate(constant)["abstention"]["production"]["auroc"] == 0.5  # what the application's retriever reports today


def test_the_no_evidence_gate_counts_empty_results_on_the_right_questions():
    gate = qa.evaluate(collect(production=_empty, use_production=False))["systems"]["production"]["no_evidence_gate"]
    assert gate["refuses_unanswerable"] == 1.0 and gate["starves_answerable"] == 1.0  # refuses everything: right and wrong alike
    gate = qa.evaluate(collect(production=_perfect, use_production=False))["systems"]["production"]["no_evidence_gate"]
    assert gate["refuses_unanswerable"] == 1.0 and gate["starves_answerable"] == 0.0  # perfect: nothing for the unanswerable
    gate = qa.evaluate(collect(production=_everything_but_the_answer, use_production=False))["systems"]["production"]["no_evidence_gate"]
    assert gate["refuses_unanswerable"] == 0.0  # something always comes back, so the gate never fires


def test_qa_context_scores_follow_from_the_chunks_handed_over():
    perfect = qa.evaluate(collect(production=_perfect, use_production=False))["systems"]["production"]["overall"]
    assert perfect["fact_recall@5"]["mean"] == pytest.approx(1.0) and perfect["full_context@5"]["mean"] == 1.0
    blind = qa.evaluate(collect(production=_everything_but_the_answer, use_production=False))["systems"]["production"]["overall"]
    assert blind["fact_recall@5"]["mean"] < 0.2 and blind["context_precision@5"]["mean"] == 0.0


def test_fact_recall_accepts_any_listed_variant_and_ignores_spacing_and_case():
    assert qa.fact_recall("pH 值控制在 6.5～7.2，温度 35 ℃", ["35 ℃", "pH 值控制在 6.5～7.2"]) == 1.0
    assert qa.fact_recall("温度 35 ℃", ["35 ℃", "pH 值控制在 6.5～7.2"]) == 0.5
    assert qa.fact_recall("at 35 ℃", [["35 ℃", "35 degrees"], ["pH 7"]]) == 0.5
    assert qa.fact_recall("ASTM  b117", ["astm B117"]) == 1.0
    assert qa.fact_recall("whatever", []) == 1.0


@pytest.mark.parametrize(
    "text",
    ["无法回答这个问题", "没有足够的证据支持结论", "未找到相关资料", "证据不足", "Insufficient evidence to answer.", "I cannot find that in the sources.", "No relevant information was retrieved."],
)
def test_a_refusal_is_recognised_in_either_language(text):
    assert qa.declined(text)


@pytest.mark.parametrize("text", ["中性盐雾试验温度为 35 ℃。", "The test runs at 35 °C for 720 h.", ""])
def test_an_answer_is_not_mistaken_for_a_refusal(text):
    assert not qa.declined(text)


def test_answers_are_scored_for_facts_for_answering_and_for_declining():
    collected = collect(production=_perfect, use_production=False)

    def extractive(question: str, snippets: list[str]) -> str:
        return " ".join(snippets) if snippets else "没有足够的证据"

    scored = qa.score_answers(collected, extractive)
    assert scored["answer_fact_recall"]["mean"] == pytest.approx(1.0)
    assert scored["answers_answerable"] == 1.0
    assert scored["declines_unanswerable"] == 1.0  # _perfect hands an unanswerable question nothing, and this answerer then declines

    def always_declines(question: str, snippets: list[str]) -> str:
        return "无法回答"

    scored = qa.score_answers(collected, always_declines)
    assert scored["answers_answerable"] == 0.0 and scored["declines_unanswerable"] == 1.0 and scored["answer_fact_recall"]["mean"] == 0.0

    def never_declines(question: str, snippets: list[str]) -> str:
        return "这是确定的答案。"

    assert qa.score_answers(collected, never_declines)["declines_unanswerable"] == 0.0


# ── the application's retriever, as the evaluation found it ───────────────────────────────────────


@pytest.fixture(scope="module")
def production():
    collected = collect()
    return evaluate(collected), qa.evaluate(collected)


def test_the_production_retriever_finds_the_answer_in_the_top_five_for_most_questions(production):
    ret, _ = production
    overall = ret["systems"]["production"]["overall"]
    assert overall["success@5"]["mean"] >= 0.85
    assert overall["success@1"]["mean"] >= 0.65
    assert overall["success@5"]["mean"] > ret["systems"]["random"]["overall"]["success@5"]["mean"] + 0.6


def test_it_keeps_up_with_a_plain_bm25_on_questions_that_repeat_the_documents_words(production):
    ret, _ = production
    by = {s: ret["systems"][s]["by_category"] for s in ("production", "bm25_reference")}
    assert by["production"]["lexical"]["success@1"] >= 0.9
    assert by["production"]["hard_negative"]["success@1"] >= by["bm25_reference"]["hard_negative"]["success@1"] - 0.12
    assert by["production"]["numeric"]["success@1"] >= by["bm25_reference"]["numeric"]["success@1"] - 0.12


def test_numeric_questions_asked_in_chinese_reach_the_english_document_that_answers_them(production):
    """The tokenizer bug this harness found: ``EEW`` / ``ASTM`` in a Chinese question never matched the English text."""
    ret, _ = production
    assert ret["systems"]["production"]["by_category"]["numeric"]["recall@10"] == 1.0


def test_every_question_gets_something_back_so_the_empty_evidence_gate_never_fires_today(production):
    """Pinned as a *finding*, not an aspiration: if retrieval ever starts returning nothing for questions it cannot answer,
    this fails, and ``evals/README.md`` (what it says about the current state) needs the good news."""
    _, answers = production
    gate = answers["systems"]["production"]["no_evidence_gate"]
    assert gate["starves_answerable"] == 0.0
    assert gate["refuses_unanswerable"] == 0.0


def test_the_context_has_the_facts_for_most_questions(production):
    _, answers = production
    overall = answers["systems"]["production"]["overall"]
    assert overall["fact_recall@5"]["mean"] >= 0.8 and overall["full_context@5"]["mean"] >= 0.75


def test_the_retrieval_config_says_what_was_run(production):
    ret, _ = production
    config = ret["config"]
    assert config["corpus_documents"] == len(DOCS) and config["queries"] == len(QUERIES) and config["k"] == 10
    assert "vector_channel" in config and "bm25_tokenizer" in config


def test_the_committed_baseline_matches_what_retrieval_and_qa_measure_now(production):
    """A baseline that lags the datasets or the code makes every later comparison noise (it did, once: a query was given its facts
    after the baseline was taken, and ten QA numbers sat 0.01 off). Retrieval and QA are deterministic - fixed corpus, no sampling -
    so they must agree to rounding. Tighter than the 0.03 a run reports as a regression; run ``python -m evals --update-baseline``
    after a change that is meant to move them. (Parsing and optimisation depend on which extras and library versions are installed,
    so they are not pinned here.)"""
    import json

    from evals import report as R
    from evals import run as runner

    ret, answers = production
    if ret["config"].get("vector_channel"):
        pytest.skip("an embedding model is installed here: the baseline was taken without the vector channel")
    baseline = json.loads(runner.BASELINE.read_text(encoding="utf-8"))["metrics"]
    now = R.headline({"suites": {"retrieval": ret, "qa": answers}})
    assert now, "nothing was measured"
    stale = {n: (baseline[n]["value"], m.value) for n, m in now.items() if abs(baseline[n]["value"] - m.value) > 0.005}
    assert not stale, f"the baseline is out of date (python -m evals --update-baseline): {stale}"


def test_documents_are_collapsed_from_chunks_to_ids_at_their_best_rank():
    assert M.dedupe_keep_first([h.doc_id for h in [Hit("a", 1, ""), Hit("b", 1, ""), Hit("a", 1, "")]]) == ["a", "b"]
    assert retrieval.K == 10
