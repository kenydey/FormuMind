"""Tests for grounded recommend pipeline."""
from __future__ import annotations

from app.config import get_settings
from app.domain.schemas import Evidence, ProductDomain, Requirement
from app.pipeline import workflow
from app.services import colbert_store


def test_run_research_returns_grounded_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_COLBERT_INDEX_DIR", str(tmp_path / "idx"))
    get_settings.cache_clear()

    colbert_store.index_evidence(
        [
            Evidence(
                source="literature",
                identifier="doi:1",
                title="Waterborne epoxy primer",
                snippet="Zinc phosphate and polyamide hardener formulation.",
                relevance=0.5,
            )
        ],
    )

    req = Requirement(domain=ProductDomain.anticorrosion_coating, salt_spray_hours=500)
    result = workflow.run_research(
        req,
        pre_sources=[],
        query="waterborne epoxy primer zinc phosphate",
    )
    assert result.evidence is not None
    assert isinstance(result.recommended, list)
    get_settings.cache_clear()
