"""Tests for ColBERT store fallback path (no ragatouille required)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.config import Settings
from app.domain.schemas import Evidence
from app.services import colbert_store


@pytest.fixture
def colbert_settings():
    with tempfile.TemporaryDirectory() as tmp:
        yield Settings(colbert_index_dir=str(Path(tmp) / "idx"))


def test_index_and_search_evidence(colbert_settings):
    ev = [
        Evidence(
            source="patents",
            identifier="US123",
            title="Zinc phosphate anti-corrosion primer",
            snippet="Desmodur BL 3175 blocked isocyanate crosslinker for epoxy systems.",
            relevance=0.5,
        )
    ]
    count = colbert_store.index_evidence(ev, settings=colbert_settings)
    assert count >= 1
    hits = colbert_store.search("Desmodur crosslinker epoxy", k=3, settings=colbert_settings)
    assert hits
    assert any("Desmodur" in h.passage or "Desmodur" in h.evidence.title for h in hits)


def test_bootstrap_seed_corpus(colbert_settings):
    n = colbert_store.bootstrap_seed_corpus(colbert_settings)
    assert n >= 10
    hits = colbert_store.search("Bisphenol-A epoxy", k=2, settings=colbert_settings)
    assert len(hits) >= 1
