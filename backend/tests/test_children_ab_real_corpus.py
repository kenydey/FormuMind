"""C-7: children retrieval real-corpus A/B (default-switch decision).

Root cause of the old synthetic A/B's null result (test_phase2_children_ab.py):
it scored parents with raw keyword overlap -- no length normalization. Under
raw overlap a child's token set is always a subset of its parent's, so
``best_child <= parent_score`` always, ``promoted = max(best_child,
parent_score) == parent_score``, and ``children_rerank`` was a mathematical
no-op. 1.000 vs 1.000 was preordained, not measured.

This A/B uses the production scoring path for the parent arm:
``hybrid_search_scored`` with real BM25Okapi (length-normalized), mirroring
``kb_index.py`` probe wiring (pool = k * candidate_mult, then children
re-rank, top-k presented). Only under length-normalized parent scoring can a
single strong sentence promote its parent above a mediocre whole-block match.

Corpus (tests/fixtures/children_ab_real_corpus.json): 20 annotated +
200 distractor REAL KB wiki chemistry dossiers (silane conversion film,
anticorrosion -- the closest thing to real long chemistry docs in this env;
no external papers are ingested here). Queries + key claims were written
before any retrieval run (no peeking); every keyword verified present in its
target chunk.

Decision rule (unchanged): children_cov > parent_cov + 0.05 -> enable;
children_cov >= parent_cov - 0.01 is a hard no-regression gate.

C-7 MEASURED RESULT (2026-10-01): n=20, parent_cov=0.900,
children_cov=0.900 -- flip condition NOT met. Diagnostic target
recall@6: parent=0.700, children=0.700, also identical. The re-rank
mechanism was verified active (it does reorder the pool), but yields no
metric gain on this corpus. kb_children_retrieval_enabled stays False
(default off, opt-in retained). Corpus caveat: this env has no externally
ingested long chemistry papers; the corpus is real KB wiki dossiers
(silane/anticorrosion, long, natural dilution). Near-duplicate dossiers
mute the keyword-coverage signal, which is reported as a limitation, not
a reason to re-cut the metric post-hoc.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory

_FIXTURE = Path(__file__).with_name("fixtures") / "children_ab_real_corpus.json"
_TOP_K = 6
_MULT = 3
_PUNCT = r"[\s\u3000\-\u2013\u2014_.,;:!?\u3001\u3002\uff0c\uff1b\uff1a\uff01\uff1f\uff08\uff09()\[\]\u3010\u3011\"'“”‘’·/\\]+"


def _norm(text: str) -> str:
    return re.sub(_PUNCT, "", (text or "").lower())


def _coverage(key_claims, texts) -> float:
    normed = [_norm(t) for t in texts]
    claims = key_claims or []
    if not claims:
        return 1.0
    hit = 0
    for c in claims:
        kws = c.get("keywords") or []
        m = sum(1 for kw in kws if any(_norm(kw) in t for t in normed))
        if kws and m / len(kws) >= 0.5:
            hit += 1
    return hit / len(claims)


@pytest.fixture()
def corpus_db(tmp_path, monkeypatch):
    """Seed an isolated DB with the real-corpus fixture (220 chunks)."""
    import app.db.chunk_store as chunk_store_mod
    import app.db.database as db_mod
    import app.db.source_store as source_store_mod
    from app.db.source_store import SourceStore

    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()

    with open(_FIXTURE, encoding="utf-8") as f:
        fx = json.load(f)

    engine = make_engine(f"sqlite:///{tmp_path}/c7.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src = SourceStore(factory)
    chk = ChunkStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    monkeypatch.setattr(db_mod, "default_session_factory", lambda: factory)

    sid = src.create(
        filename="children_ab_corpus.md",
        title="C-7 real corpus",
        # NOTE: source_kind="local", not "wiki": the retrieval content gate
        # deliberately excludes wiki-track sources (Track B exclusion); the
        # A/B measures scoring, not that policy. Chunk texts are unchanged.
        source_kind="local",
        full_text="(fixture)",
        content_hash="c7-fixture",
    )
    chunks = [{"text": t["text"]} for t in fx["targets"]]
    chunks += [{"text": d["text"]} for d in fx["distractors"]]
    chk.replace_for_source(sid, chunks)

    yield fx
    get_settings.cache_clear()


def _run_ab(fx):
    from app.services.children_retrieval import children_rerank
    from app.services.hybrid_search import hybrid_search_scored

    tot_parent = 0.0
    tot_child = 0.0
    n = 0
    for tgt in fx["targets"]:
        q = tgt["query"]
        parent_scored = hybrid_search_scored(q, top_k=_TOP_K)
        parent_texts = [s.chunk.text for s in parent_scored]
        tot_parent += _coverage(tgt["key_claims"], parent_texts)
        pool = hybrid_search_scored(q, top_k=_TOP_K * _MULT)
        reranked = children_rerank(q, pool, candidate_mult=_MULT, top_k=_TOP_K)
        child_texts = [s.chunk.text for s in reranked]
        tot_child += _coverage(tgt["key_claims"], child_texts)
        n += 1
    return (tot_parent / n if n else 0.0, tot_child / n if n else 0.0, n)


def test_ab_children_vs_parent_real_corpus(corpus_db):
    """C-7 decision test: real corpus, production scoring, shared 220-doc pool.

    Hard gate: children must never regress parent by more than 0.01.
    The enable/disable decision is reported (print) and recorded here;
    flipping ``kb_children_retrieval_enabled`` requires the +0.05 margin.
    """
    parent_cov, child_cov, n = _run_ab(corpus_db)
    print(
        "\nC-7 A/B (real corpus): n=%d parent_cov=%.3f children_cov=%.3f"
        % (n, parent_cov, child_cov)
    )
    assert child_cov >= parent_cov - 0.01, (
        "children regression: parent=%.3f children=%.3f" % (parent_cov, child_cov)
    )
