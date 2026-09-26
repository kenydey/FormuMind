"""P1 #15: cross-encoder / unified rerank_scored with applied marking."""
from __future__ import annotations

from types import SimpleNamespace

from app.domain.schemas import Evidence
from app.services import rag


def _ev(i: int, title: str, snippet: str, rel: float = 0.5) -> Evidence:
    return Evidence(
        source="kb",
        identifier=f"id-{i}",
        title=title,
        snippet=snippet,
        relevance=rel,
    )


def test_rerank_cross_encoder_scored_orders_by_predict(monkeypatch):
    cands = [
        _ev(0, "unrelated", "cooking recipes", 0.9),
        _ev(1, "epoxy", "salt spray coating", 0.2),
        _ev(2, "other", "random text", 0.5),
    ]

    class FakeCE:
        def predict(self, pairs):
            # Higher score for epoxy/salt spray
            out = []
            for _q, doc in pairs:
                out.append(10.0 if "salt spray" in doc else 0.1)
            return out

    monkeypatch.setattr(rag, "_load_cross_encoder", lambda name: FakeCE())
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(cross_encoder_model="fake/ce"),
    )
    items, applied = rag.rerank_cross_encoder_scored("coating", cands, k=2)
    assert applied is True
    assert items[0].evidence.identifier == "id-1"


def test_rerank_cross_encoder_failure_marks_not_applied(monkeypatch):
    cands = [_ev(0, "a", "aa"), _ev(1, "b", "bb")]

    def boom(name):
        raise RuntimeError("no model")

    monkeypatch.setattr(rag, "_load_cross_encoder", boom)
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(cross_encoder_model="fake/ce"),
    )
    items, applied = rag.rerank_cross_encoder_scored("q", cands, k=2)
    assert applied is False
    assert [it.evidence.identifier for it in items] == ["id-0", "id-1"]


def test_rerank_scored_auto_prefers_cross_encoder(monkeypatch):
    cands = [_ev(0, "a", "aa", 0.1), _ev(1, "b", "salt spray", 0.2)]

    class FakeCE:
        def predict(self, pairs):
            return [0.0, 5.0]

    monkeypatch.setattr(rag, "cross_encoder_available", lambda: True)
    monkeypatch.setattr(rag, "_load_cross_encoder", lambda name: FakeCE())
    monkeypatch.setattr(
        rag,
        "llm_rerank_scored",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM should not run")),
    )
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(
            cross_encoder_rerank_enabled=True,
            cross_encoder_model="fake/ce",
            search_rerank_backend="auto",
        ),
    )
    items, meta = rag.rerank_scored("q", cands, k=2)
    assert meta["applied"] is True
    assert meta["backend"] == "cross_encoder"
    assert items[0].evidence.identifier == "id-1"


def test_rerank_scored_falls_back_to_llm(monkeypatch):
    cands = [_ev(0, "a", "aa"), _ev(1, "b", "bb")]

    def fake_llm(query, candidates, k=6, req=None):
        items = [
            rag.RerankScoredItem(evidence=candidates[1], score=0.9, original_index=1),
            rag.RerankScoredItem(evidence=candidates[0], score=0.1, original_index=0),
        ]
        return items[:k], True

    monkeypatch.setattr(rag, "cross_encoder_available", lambda: False)
    monkeypatch.setattr(rag, "llm_rerank_scored", fake_llm)
    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: SimpleNamespace(
            cross_encoder_rerank_enabled=True,
            cross_encoder_model="fake/ce",
            search_rerank_backend="auto",
        ),
    )
    items, meta = rag.rerank_scored("q", cands, k=2)
    assert meta["applied"] is True
    assert meta["backend"] == "llm"
    assert items[0].evidence.identifier == "id-1"


def test_llm_rerank_warns_when_not_applied(monkeypatch, caplog):
    import logging

    cands = [_ev(0, "a", "aa"), _ev(1, "b", "bb")]
    monkeypatch.setattr(
        rag,
        "llm_rerank_scored",
        lambda *a, **k: (
            [
                rag.RerankScoredItem(evidence=cands[0], score=0.5, original_index=0),
                rag.RerankScoredItem(evidence=cands[1], score=0.5, original_index=1),
            ],
            False,
        ),
    )
    with caplog.at_level(logging.WARNING, logger="app.services.rag"):
        out = rag.llm_rerank("q", cands, k=2)
    assert len(out) == 2
    assert any("not applied" in r.message for r in caplog.records)
