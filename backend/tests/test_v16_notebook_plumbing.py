"""v16 P1-3: notebook 接线补"源" —— 端到端可达测试。

- run_research_graph(notebooklm_notebook_id=...) → state 含该键
- run_research_graph 未显式传入时从项目工作区解析（fail-open）
- DeepResearchEngine.search(notebooklm_notebook_id=...) → retrieve 收到
"""

from unittest.mock import MagicMock, patch

from app.domain.schemas import Requirement
from app.pipeline.research_graph import run_research_graph


def _req() -> Requirement:
    return Requirement(domain="anticorrosion_coating")


def test_v16_run_research_graph_writes_notebook_id_to_state():
    """显式传入的 notebook ID 必须进 state（fallback_node 可读）。"""
    with patch(
        "app.pipeline.research_graph._run_crag_retrieval", side_effect=lambda s, *a: s
    ):
        state = run_research_graph(
            "test topic", req=_req(), mode="recommend", notebooklm_notebook_id="nb-123"
        )
    assert state.get("notebooklm_notebook_id") == "nb-123"


def test_v16_run_research_graph_resolves_notebook_from_project_workspace():
    """未显式传入时从项目工作区解析（fail-open：查不到则为 None）。"""
    detail = MagicMock()
    detail.workspace.notebooklm_notebook_id = "nb-proj-456"
    req = _req()
    req.project_id = "proj-1"
    with (
        patch(
            "app.pipeline.research_graph._run_crag_retrieval",
            side_effect=lambda s, *a: s,
        ),
        patch(
            "app.db.project_store.get_project_store",
            return_value=MagicMock(get=MagicMock(return_value=detail)),
        ),
    ):
        state = run_research_graph("test topic", req=req, mode="recommend")
    assert state.get("notebooklm_notebook_id") == "nb-proj-456"


def test_v16_run_research_graph_workspace_lookup_fail_open():
    """工作区查询抛异常时不中断（None = 用全局配置）。"""
    req = _req()
    req.project_id = "proj-1"
    with (
        patch(
            "app.pipeline.research_graph._run_crag_retrieval",
            side_effect=lambda s, *a: s,
        ),
        patch(
            "app.db.project_store.get_project_store", side_effect=RuntimeError("db down")
        ),
    ):
        state = run_research_graph("test topic", req=req, mode="recommend")
    assert state.get("notebooklm_notebook_id") is None


def test_v16_deep_research_search_passes_notebook_to_retrieve():
    """DeepResearchEngine.search 的 notebook 形参透传给 retrieve。"""
    from app.services.deep_research.engine import DeepResearchEngine
    from app.services.deep_research.models import ExpandedQuery

    captured = {}

    def fake_retrieve(self, topic, **kw):
        captured.update(kw)
        return [], ExpandedQuery(intent="test")

    eng = DeepResearchEngine.__new__(DeepResearchEngine)
    eng._settings = MagicMock()
    eng._settings.search_total_limit = 10
    eng._settings.search_limit_per_source = 5
    eng._settings.get_active_api_key.return_value = None

    with patch.object(DeepResearchEngine, "retrieve", fake_retrieve):
        eng.search("test topic", notebooklm_notebook_id="nb-789")
    assert captured.get("notebooklm_notebook_id") == "nb-789"
