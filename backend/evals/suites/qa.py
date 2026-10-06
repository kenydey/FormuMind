"""Question answering: what reaches the answerer, and does a question nobody can answer get an empty-handed system?

An answer is only as good as the context it was written from, and the context is what retrieval returned. This suite scores
that context, with no model in the loop (so it runs offline, in CI, deterministically), on the same 52 documents and 86
questions as the retrieval suite:

* ``fact_recall@k`` - of the facts a correct answer needs (``facts`` in the dataset: a figure, a condition, a verdict), the share
  that is in the text of the top ``k`` chunks; ``full_context@k`` - the share of questions whose top-``k`` text holds all of them;
* ``context_precision@5`` - of the five chunks handed to the answerer, how many belong to a relevant document: the rest is
  distraction the model has to read past, and pay for;
* ``no_evidence_gate`` - the chat endpoint refuses outright when it has no evidence at all (``if not citations``). So the
  question is how often retrieval returns nothing: for the unanswerable questions (the gate's recall - it should be high) and
  for the answerable ones (starved - it should be zero).

What this does not measure is the answer itself. ``score_answers`` does, for an ``answerer(question, snippets) -> text`` the
caller supplies - the production chat path needs a live model, which an offline suite has not got; pass one with
``python -m evals --suite qa --answerer package.module:function`` to score fact recall in the answer, how often an
unanswerable question is declined, and how often an answerable one is. A stub answerer is what the tests use.
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Sequence

from .. import metrics as M
from .retrieval import Collected, Hit

KS = (1, 3, 5)
Answerer = Callable[[str, list[str]], str]

# What a system says when it declines. Deliberately broad: a refusal worded differently is still a refusal.
_DECLINES = re.compile(
    r"无法(回答|确定|给出|找到)|没有(足够|相关|找到)|未(找到|检索到|能找到)|不足以|证据不足|暂无|"
    r"\b(insufficient|not enough|cannot (find|answer|determine)|can't (find|answer)|no (relevant )?(information|evidence)|"
    r"unable to (find|answer))\b",
    re.IGNORECASE,
)


def _fact_present(text_norm: str, fact: str | Sequence[str]) -> bool:
    variants = [fact] if isinstance(fact, str) else list(fact)
    return any(M.normalise_cell(v) in text_norm for v in variants)


def fact_recall(text: str, facts: Sequence) -> float:
    if not facts:
        return 1.0
    norm = M.normalise_cell(text)
    return sum(_fact_present(norm, f) for f in facts) / len(facts)


def declined(answer: str) -> bool:
    return bool(_DECLINES.search(answer or ""))


def _context(hits: list[Hit], k: int) -> str:
    return "\n".join(h.snippet for h in hits[:k])


def evaluate(collected: Collected) -> dict:
    answerable = [q for q in collected.queries if q.answerable and q.facts]
    unanswerable = [q for q in collected.queries if not q.answerable]
    systems: dict[str, dict] = {}
    for system, by_query in collected.hits.items():
        if system == "random":
            continue  # a coin toss has no context worth scoring beyond what the retrieval suite already says
        per_cat: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        overall: dict[str, list[float]] = defaultdict(list)
        for q in answerable:
            hits = by_query[q.id]
            row: dict[str, float] = {}
            for k in KS:
                row[f"fact_recall@{k}"] = fact_recall(_context(hits, k), q.facts)
            for k in (3, 5):
                row[f"full_context@{k}"] = 1.0 if row[f"fact_recall@{k}"] >= 1.0 else 0.0
            top5 = hits[:5]
            row["context_precision@5"] = (sum(h.doc_id in q.relevant for h in top5) / len(top5)) if top5 else 0.0
            for name, value in row.items():
                per_cat[q.category][name].append(value)
                overall[name].append(value)
        systems[system] = {
            "overall": {name: M.summarise(values) for name, values in overall.items()},
            "by_category": {
                cat: {name: round(M.mean(values), 4) for name, values in rows.items()} | {"n": len(rows["fact_recall@1"])}
                for cat, rows in sorted(per_cat.items())
            },
            "no_evidence_gate": {
                # chat refuses when retrieval is empty: recall on questions that deserve a refusal, false refusals on the rest
                "refuses_unanswerable": round(M.mean([0.0 if by_query[q.id] else 1.0 for q in unanswerable]), 4),
                "starves_answerable": round(M.mean([0.0 if by_query[q.id] else 1.0 for q in collected.queries if q.answerable]), 4),
                "n_unanswerable": len(unanswerable),
            },
        }
    return {"suite": "qa", "mode": "context", "config": collected.config, "systems": systems}


def score_answers(collected: Collected, answerer: Answerer, *, system: str = "production", k: int = 5) -> dict:
    """The answer-level score of ``answerer`` fed the top-``k`` chunks the ``system`` retrieved. Needs a model; used by the CLI's --answerer."""
    by_query = collected.hits[system]
    recalls: list[float] = []
    answered: list[float] = []
    refused: list[float] = []
    for q in collected.queries:
        snippets = [h.snippet for h in by_query[q.id][:k]]
        text = answerer(q.text, snippets)
        if q.answerable:
            answered.append(0.0 if declined(text) else 1.0)
            if q.facts:
                recalls.append(fact_recall(text, q.facts))
        else:
            refused.append(1.0 if declined(text) else 0.0)
    return {
        "system": system,
        "k": k,
        "answer_fact_recall": M.summarise(recalls),
        "answers_answerable": round(M.mean(answered), 4),
        "declines_unanswerable": round(M.mean(refused), 4) if refused else None,
        "n_questions": len(collected.queries),
    }


def run(*, collected: Collected | None = None, answerer: Answerer | None = None, **kwargs) -> dict:
    from .retrieval import collect

    collected = collected or collect(**kwargs)
    result = evaluate(collected)
    if answerer is not None:
        result["answers"] = score_answers(collected, answerer)
        result["mode"] = "context+answers"
    return result

