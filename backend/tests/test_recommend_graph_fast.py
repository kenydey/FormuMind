"""Fast offline recommend graph — mode=recommend skips deep-research LLM path."""
from __future__ import annotations

import time
from unittest.mock import patch

from app.config import get_settings
from app.domain.schemas import Evidence, ProductDomain, Requirement
from app.pipeline.research_graph import run_research_graph
from app.services import colbert_store


def test_recommend_mode_completes_quickly_offline(tmp_path, monkeypatch):
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
    t0 = time.perf_counter()
    with patch("app.services.deep_research.engine.DeepResearchEngine.report_agent") as mock_report:
        state = run_research_graph(
            topic="waterborne epoxy primer zinc phosphate",
            req=req,
            query="waterborne epoxy primer zinc phosphate",
            mode="recommend",
        )
    elapsed = time.perf_counter() - t0

    mock_report.assert_not_called()
    assert elapsed < 5.0, f"recommend graph took {elapsed:.1f}s"
    assert state.get("recommended"), "expected non-empty recommended list"
    assert state.get("stage") == "recommend"
    get_settings.cache_clear()
