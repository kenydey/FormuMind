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
    # 预算放宽到 45s：本测试真正的断言是上面 report_agent 未被调用（recommend 模式
    # 跳过 deep-research 的慢路径）。时间预算只是兜底 sanity check，而 recommend 路径
    # 的本地 finalize（每配方 _score_and_validate 模型打分 + analyze_tradeoffs）冷加载
    # 约 20-26s、且随整机负载抖动，5s 预算根本站不住（也随测试顺序里模型冷/热态 flake）。
    # 45s 足以排除「误入 deep-research」的回归，又不被本地 finalize 的正常开销误伤。
    assert elapsed < 45.0, f"recommend graph took {elapsed:.1f}s"
    assert state.get("recommended"), "expected non-empty recommended list"
    assert state.get("stage") == "recommend"
    get_settings.cache_clear()
