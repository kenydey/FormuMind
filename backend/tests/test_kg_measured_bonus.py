"""v3B + metric-aware KG measured ranking.

Binary presence bonus remains the fallback when no target-metric hit exists.
When objectives are passed, mat→prop:{metric} measured edges drive
good / presence / poor soft factors.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.entity_store import EntityStore
from app.domain.schemas import Formulation, Ingredient, ObjectiveSpec, ProductDomain


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _store(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path}/kg_measured.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    store = EntityStore(factory)
    import app.db.entity_store as es_mod

    monkeypatch.setattr(es_mod, "_store", store)
    return store


def _form(*material_names: str, score: float = 1.0) -> Formulation:
    return Formulation(
        name="t",
        domain=ProductDomain.anticorrosion_coating,
        ingredients=[Ingredient(name=n, role="additive", weight_pct=10) for n in material_names],
        rationale="",
        predicted={},
        predicted_std={},
        score=score,
        warnings=[],
    )


def test_measured_material_gets_bonus(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    with store._session_factory() as s:
        store.upsert_entity(s, id="matA", canonical_name="Zinc phosphate", kind="chemical")
        store.upsert_entity(s, id="matB", canonical_name="Epoxy resin", kind="chemical")
        store.upsert_entity(s, id="propX", canonical_name="salt_spray_hours", kind="property")
    # 给 matA 一个 measured 证据（任意 relation 的 extraction_method=measured）
    with store._session_factory() as s:
        store.merge_semantic_link(
            s,
            src_entity_id="matA",
            dst_entity_id="propX",
            link_type="measured_performance",
            confidence=0.8,
            evidence_ref={
                "source_id": "measured:campaign_1",
                "extraction_method": "measured",
                "sentence": "实测 800h",
            },
            extraction_method="measured",
        )

    from app.services.kg_recommend_score import kg_compat_adjust

    f_with = _form("Zinc phosphate", "Epoxy resin", score=1.0)
    f_without = _form("Epoxy resin", "Acrylic resin", score=1.0)
    # 为后者也补全可解析实体，避免 <2 resolved 的 pass 路径干扰
    with store._session_factory() as s:
        store.upsert_entity(s, id="matC", canonical_name="Acrylic resin", kind="chemical")

    chk_with = kg_compat_adjust(f_with)
    chk_without = kg_compat_adjust(f_without)

    assert "Zinc phosphate" in chk_with.measured_materials
    assert f_with.score == pytest.approx(1.15)
    assert "实测验证加成" in " ".join(f_with.warnings)
    assert f_with.kg_compat and "measured_materials" in f_with.kg_compat
    # 无实测材料的配方不应加成
    assert chk_without.measured_materials == []
    assert f_without.score == pytest.approx(1.0)


def test_inhibits_still_penalizes_despite_measured(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    with store._session_factory() as s:
        store.upsert_entity(s, id="matA", canonical_name="Zinc phosphate", kind="chemical")
        store.upsert_entity(s, id="matB", canonical_name="Epoxy resin", kind="chemical")
        store.upsert_entity(s, id="propX", canonical_name="p", kind="property")
    with store._session_factory() as s:
        store.merge_semantic_link(
            s,
            src_entity_id="matA",
            dst_entity_id="propX",
            link_type="measured_performance",
            confidence=0.8,
            evidence_ref={
                "source_id": "measured:campaign_1",
                "extraction_method": "measured",
                "sentence": "x",
            },
            extraction_method="measured",
        )
        # 再加一个 inhibits 使配方 infeasible
        store.merge_semantic_link(
            s,
            src_entity_id="matA",
            dst_entity_id="matB",
            link_type="inhibits",
            confidence=0.9,
            evidence_ref={
                "source_id": "lit-1",
                "extraction_method": "rule",
                "sentence": "不相容",
            },
            extraction_method="rule",
        )

    from app.services.kg_recommend_score import kg_compat_adjust

    f = _form("Zinc phosphate", "Epoxy resin", score=1.0)
    chk = kg_compat_adjust(f)
    assert not chk.feasible
    # 不相容时只罚不奖，measured 加成被跳过
    assert f.score == pytest.approx(0.5)
    assert f.kg_compat is not None
    assert "Zinc phosphate" in f.kg_compat["measured_materials"]


def test_target_metric_good_beats_unrelated_measured(tmp_path, monkeypatch):
    """Salt-spray objective: good mat→prop:salt_spray_hours beats gloss-only measured."""
    store = _store(tmp_path, monkeypatch)
    with store._session_factory() as s:
        store.upsert_entity(s, id="mat:zinc_phosphate", canonical_name="Zinc phosphate", kind="chemical")
        store.upsert_entity(s, id="mat:epoxy_resin", canonical_name="Epoxy resin", kind="chemical")
        store.upsert_entity(s, id="mat:filler", canonical_name="Barium sulfate", kind="chemical")
        store.upsert_entity(s, id="prop:salt_spray_hours", canonical_name="salt_spray_hours", kind="property")
        store.upsert_entity(s, id="prop:gloss", canonical_name="gloss", kind="property")
    with store._session_factory() as s:
        store.merge_semantic_link(
            s,
            src_entity_id="mat:zinc_phosphate",
            dst_entity_id="prop:salt_spray_hours",
            link_type="measured_performance",
            confidence=0.85,
            evidence_ref={
                "source_id": "measured:campaign_1",
                "extraction_method": "measured",
                "metric": "salt_spray_hours",
                "measured_value": 900.0,
                "sentence": "实测 Zinc phosphate: 盐雾时长=900.0",
                "granularity": "material",
            },
            extraction_method="measured",
        )
        store.merge_semantic_link(
            s,
            src_entity_id="mat:filler",
            dst_entity_id="prop:gloss",
            link_type="measured_performance",
            confidence=0.85,
            evidence_ref={
                "source_id": "measured:campaign_2",
                "extraction_method": "measured",
                "metric": "gloss",
                "measured_value": 95.0,
                "sentence": "实测 Barium sulfate: 光泽=95.0",
                "granularity": "material",
            },
            extraction_method="measured",
        )

    from app.services.kg_recommend_score import kg_compat_adjust

    objs = [ObjectiveSpec(metric="salt_spray_hours", direction="maximize")]
    good = _form("Zinc phosphate", "Epoxy resin", score=1.0)
    good.predicted = {"salt_spray_hours": 800.0}
    other = _form("Barium sulfate", "Epoxy resin", score=1.0)
    other.predicted = {"salt_spray_hours": 800.0}

    kg_compat_adjust(good, objectives=objs)
    kg_compat_adjust(other, objectives=objs)

    assert good.score == pytest.approx(1.12)  # metric good bonus
    assert any("目标指标实测加成" in w for w in good.warnings)
    assert good.kg_compat and good.kg_compat["measured_metric_hits"]
    # gloss-only measured under salt-spray objective → no target hit → no boost
    assert other.score == pytest.approx(1.0)
    assert not other.kg_compat.get("measured_metric_hits")
    bare = _form("Epoxy resin", "Acrylic resin", score=1.0)
    with store._session_factory() as s:
        store.upsert_entity(s, id="mat:acrylic", canonical_name="Acrylic resin", kind="chemical")
    bare.predicted = {"salt_spray_hours": 800.0}
    kg_compat_adjust(bare, objectives=objs)
    assert good.score > bare.score
    assert good.score > other.score


def test_target_metric_poor_demotes(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    with store._session_factory() as s:
        store.upsert_entity(s, id="mat:zinc_phosphate", canonical_name="Zinc phosphate", kind="chemical")
        store.upsert_entity(s, id="mat:epoxy_resin", canonical_name="Epoxy resin", kind="chemical")
        store.upsert_entity(s, id="prop:salt_spray_hours", canonical_name="salt_spray_hours", kind="property")
    with store._session_factory() as s:
        store.merge_semantic_link(
            s,
            src_entity_id="mat:zinc_phosphate",
            dst_entity_id="prop:salt_spray_hours",
            link_type="measured_performance",
            confidence=0.5,
            evidence_ref={
                "source_id": "measured:campaign_3",
                "extraction_method": "measured",
                "metric": "salt_spray_hours",
                "measured_value": 200.0,
                "sentence": "实测 Zinc phosphate: 盐雾时长=200.0",
                "granularity": "material",
            },
            extraction_method="measured",
        )

    from app.services.kg_recommend_score import kg_compat_adjust

    objs = [ObjectiveSpec(metric="salt_spray_hours", direction="maximize")]
    f = _form("Zinc phosphate", "Epoxy resin", score=1.0)
    f.predicted = {"salt_spray_hours": 800.0}  # measured 200 = 0.25×pred → poor
    kg_compat_adjust(f, objectives=objs)
    assert f.score == pytest.approx(0.92)
    assert any("实测偏弱" in w for w in f.warnings)
    assert f.kg_compat["measured_metric_hits"][0]["quality"] == "poor"
