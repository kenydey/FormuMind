"""The labelled retrieval dataset's invariants (``backend/evals/datasets``): a mislabelled query makes every number meaningless."""
from __future__ import annotations

from collections import Counter

import pytest
from evals import metrics as M
from evals.datasets import load_corpus, load_queries

CATEGORIES = {"lexical", "paraphrase", "numeric", "hard_negative", "crosslingual", "multi_doc", "unanswerable"}


@pytest.fixture(scope="module")
def docs():
    return {d.id: d for d in load_corpus()}


@pytest.fixture(scope="module")
def queries():
    return load_queries()


def test_ids_are_unique(docs, queries):
    assert len(docs) == len(load_corpus()) and len({q.id for q in queries}) == len(queries)


def test_every_judged_document_exists_and_grades_are_one_to_three(docs, queries):
    for q in queries:
        assert set(q.relevant) <= set(docs), (q.id, set(q.relevant) - set(docs))
        assert set(q.relevant.values()) <= {1, 2, 3}, q.id


def test_an_answerable_question_has_a_document_that_answers_it_and_facts_to_check(docs, queries):
    for q in queries:
        if q.answerable:
            assert 3 in q.relevant.values(), f"{q.id}: no document is graded 3"
            assert q.facts, f"{q.id}: no facts"


def test_an_unanswerable_question_has_no_judged_document_and_no_facts(queries):
    unanswerable = [q for q in queries if q.category == "unanswerable"]
    assert len(unanswerable) >= 10
    assert all(not q.relevant and not q.facts for q in unanswerable)
    assert all(q.answerable for q in queries if q.category != "unanswerable")


def test_every_fact_is_in_a_document_the_question_is_judged_against(docs, queries):
    for q in queries:
        text = " ".join(M.normalise_cell(docs[d].text) for d in q.relevant)
        for fact in q.facts:
            variants = [fact] if isinstance(fact, str) else list(fact)
            assert any(M.normalise_cell(v) in text for v in variants), f"{q.id}: {fact!r} is in none of {sorted(q.relevant)}"


def test_the_categories_are_the_documented_ones_and_each_has_company(queries):
    counts = Counter(q.category for q in queries)
    assert set(counts) == CATEGORIES
    assert min(counts.values()) >= 5, counts  # a category of one is an anecdote


def test_crosslingual_queries_ask_in_the_other_language_than_the_answer(docs, queries):
    for q in queries:
        if q.category == "crosslingual":
            gold = next(d for d, g in q.relevant.items() if g == 3)
            assert q.lang != docs[gold].lang, q.id


def test_a_hard_negative_has_neighbours_in_the_cluster_of_its_answer(docs, queries):
    sizes = Counter(d.cluster for d in docs.values())
    for q in queries:
        if q.category == "hard_negative":
            gold = next(d for d, g in q.relevant.items() if g == 3)
            assert sizes[docs[gold].cluster] >= 3, f"{q.id}: nothing similar to confuse it with"


def test_the_corpus_is_bilingual_and_every_document_has_text(docs):
    assert {d.lang for d in docs.values()} == {"zh", "en"}
    assert all(d.text.strip() and d.title.strip() for d in docs.values())
    assert len({d.title for d in docs.values()}) == len(docs)
