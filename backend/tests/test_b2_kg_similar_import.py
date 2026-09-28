"""B-2 回归：api/kg.py 的 /formulations/similar 端点在有匹配结果时必须能构造
SimilarFormulationMatch（此前漏 import → 有结果即 NameError → 500，空结果掩盖）。"""
from __future__ import annotations

from types import SimpleNamespace


def _session():
    row = SimpleNamespace(
        id=1, project_id="", domain="coating",
        factors={"ZnO": 5.0}, measured={},
    )

    class _Q:
        def filter(self, *a, **k):
            return self

        def all(self):
            return [row]

        def first(self):
            return None

    class _S:
        def query(self, model):
            return _Q()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return _S()


def test_similar_formulations_match_imported(monkeypatch):
    """构造非空匹配场景：不断言 500/NameError，且响应结构正确。"""
    import app.api.kg as kg_mod

    # 审查报告的验证式：类必须在模块命名空间可见
    assert "SimilarFormulationMatch" in vars(kg_mod)

    monkeypatch.setattr(
        "app.db.database.default_session_factory", lambda: _session
    )
    monkeypatch.setattr(
        kg_mod,
        "find_similar_formulations",
        lambda qf, exps, **kw: [
            {
                "experiment_id": 1,
                "project_id": "",
                "similarity": 0.9,
                "factors": {"ZnO": 5.0},
                "measured": {},
            }
        ],
    )

    req = kg_mod.SimilarFormulationRequest(factors={"ZnO": 5.0})
    resp = kg_mod.similar_formulations(req)  # pre-fix: NameError
    assert len(resp.matches) == 1
    m = resp.matches[0]
    assert m.experiment_id == 1
    assert m.similarity == 0.9
    assert m.factors == {"ZnO": 5.0}
    assert resp.query_factors == {"ZnO": 5.0}
