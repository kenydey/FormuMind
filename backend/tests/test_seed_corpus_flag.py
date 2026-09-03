"""Seed corpus evidence must be flagged is_seed_corpus."""
from __future__ import annotations

from app.domain.schemas import ProductDomain, Requirement
from app.services import literature


def test_patent_seed_fallback_sets_is_seed_corpus(monkeypatch):
    monkeypatch.setattr(literature, "_search_epo_patents", lambda *a, **k: [])
    monkeypatch.setattr(literature, "_online_search", lambda *a, **k: [])

    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        substrate="carbon_steel",
        salt_spray_hours=500,
        film_weight_gsm=70,
        cure_temperature_c=80,
        cleaning_efficiency=90,
        voc_limit_gpl=None,
        ph_target=None,
        notes="",
        objectives=[],
    )
    ev = literature.search_patents(req, limit=3, query="zinc")
    assert len(ev) >= 1
    assert all(e.is_seed_corpus for e in ev)
