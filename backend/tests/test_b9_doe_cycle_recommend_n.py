"""B-9 回归：
1. run_doe_cycle 传给 RecommendFormulationsRequest 的 n 必须 ≤12
   （此前 n=20 恒抛 ValidationError 被吞 → 恒回退单条 baseline）。
2. _doe_metadata 嵌套 dict 不再注入 ExperimentRow.factors —— 否则相似度
   评分 ``cv > 0`` 会 TypeError（已用真实 formulation_similarity 复现）。
"""
from __future__ import annotations

from types import SimpleNamespace


def _run_cycle(monkeypatch, runs):
    from app.services import doe_cycle_service as mod

    captured_req: list[dict] = []

    class _RecordingRequest:
        """模拟真实模型的 le=12 约束：n>12 直接抛错。"""

        def __init__(self, **kwargs):
            captured_req.append(kwargs)
            n = kwargs.get("n")
            if n is None or n > 12:
                raise ValueError(f"n={n} violates le=12")

    import app.api.formulations as form_mod

    monkeypatch.setattr(form_mod, "RecommendFormulationsRequest", _RecordingRequest)
    monkeypatch.setattr(
        form_mod,
        "recommend_formulations",
        lambda req: SimpleNamespace(
            formulations=[SimpleNamespace(factors={"resin_wt_pct": 60.0})]
        ),
    )

    class _UnavailableBaybe:
        def available(self) -> bool:
            return False

    import app.services.engines.baybe_engine as baybe_mod
    import app.services.active_learning as al_mod

    monkeypatch.setattr(baybe_mod, "BaybeCampaignEngine", _UnavailableBaybe)
    monkeypatch.setattr(
        al_mod,
        "active_learning_doe",
        lambda **kwargs: SimpleNamespace(plan=SimpleNamespace(runs=runs)),
    )

    # provenance 写链路旁路
    import app.services.provenance as prov_mod

    monkeypatch.setattr(prov_mod, "formulation_id_for", lambda f: "fid-1")
    monkeypatch.setattr(prov_mod, "link", lambda *a, **k: None)

    captured_rows: list[dict] = []

    class _ExpRow:
        def __init__(self, **kwargs):
            self.id = "exp-1"
            captured_rows.append(kwargs)

    class _Session:
        def add(self, obj):
            pass

        def flush(self):
            pass

    class _CM:
        def __enter__(self):
            return _Session()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(mod, "ExperimentRow", _ExpRow)
    monkeypatch.setattr(mod, "default_session_factory", lambda: object())
    monkeypatch.setattr(mod, "commit_session", lambda factory: _CM())

    requirement = SimpleNamespace(
        domain=SimpleNamespace(value="coating"),
        project_id="proj-1",
    )
    result = mod.run_doe_cycle(requirement)
    return result, captured_req, captured_rows


def _lhs_run():
    return SimpleNamespace(
        run_id="lhs-1",
        coded={"x1": 0.0},
        natural={"resin_wt_pct": 62.0},
        ai_suggested=True,
        infeasible=False,
        infeasible_reason=None,
    )


def test_doe_cycle_uses_n_within_model_limit(monkeypatch):
    """n 必须 ≤12：pre-fix 传 n=20 会被模型约束拒绝。"""
    result, captured_req, _ = _run_cycle(monkeypatch, [_lhs_run()])
    assert result["status"] == "success", result
    assert captured_req, "expected RecommendFormulationsRequest to be constructed"
    assert captured_req[0]["n"] <= 12
    assert captured_req[0]["n"] == 12


def test_doe_cycle_persisted_factors_are_pure_numeric(monkeypatch):
    """持久化 factors 不含嵌套 dict；与真实相似度评分联调不抛 TypeError。"""
    from app.services.kg.formulation_similarity import formulation_similarity

    result, _, captured_rows = _run_cycle(monkeypatch, [_lhs_run()])
    assert result["status"] == "success", result
    assert captured_rows
    for row in captured_rows:
        factors = row["factors"]
        assert "_doe_metadata" not in factors
        for k, v in factors.items():
            assert not k.startswith("_"), f"metadata key leaked: {k}"
            assert isinstance(v, (int, float)), f"{k}={v!r} not numeric"
        # B-9 根因复现：旧 factors（含 _doe_metadata 嵌套 dict）在此 TypeError
        sim = formulation_similarity({"resin_wt_pct": 60.0}, factors)
        assert 0.0 <= sim <= 1.0


def test_doe_cycle_recommendation_failure_still_fail_open(monkeypatch):
    """推荐链路抛错仍 fail-open 回退 baseline（error 日志已打）。"""
    import app.api.formulations as form_mod
    import app.domain.knowledge as knowledge

    monkeypatch.setattr(
        form_mod,
        "RecommendFormulationsRequest",
        lambda **kw: (_ for _ in ()).throw(ValueError("boom")),
    )
    monkeypatch.setattr(
        knowledge,
        "baseline_formulation",
        lambda req: SimpleNamespace(factors={"resin_wt_pct": 55.0}),
    )

    result, _, captured_rows = _run_cycle(monkeypatch, [_lhs_run()])
    # LHS 仍生成实验（推荐失败只影响候选来源，不影响生成）
    assert result["status"] == "success", result
    assert captured_rows
