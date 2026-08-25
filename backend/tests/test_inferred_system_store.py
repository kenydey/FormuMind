"""Inferred-system store (P2 self-learning knowledge base) + 3-tier resolve."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.inferred_system_store import InferredSystemStore
from app.domain.formulation_systems import normalize_key
from app.domain.schemas import InferredSystem


@pytest.fixture()
def store(tmp_path, monkeypatch):
    import app.db.inferred_system_store as mod

    engine = make_engine(f"sqlite:///{tmp_path}/inferred.db")
    Base.metadata.create_all(engine)
    st = InferredSystemStore(make_session_factory(engine))
    monkeypatch.setattr(mod, "_store", st)
    yield st


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _sys(**kw) -> InferredSystem:
    defaults = dict(
        system_name="测试体系",
        must_include_roles=["resin"],
        must_exclude="no chromate",
        constraints=["pH 2-4"],
        metric_ranges={"salt_spray_hours": (500.0, 1440.0)},
        confidence="medium",
    )
    defaults.update(kw)
    return InferredSystem(**defaults)


# ── normalize_key ────────────────────────────────────────────────────────────


def test_normalize_key_strips_punctuation_and_case():
    assert normalize_key("含聚合物/树脂的乳液型") == "含聚合物树脂的乳液型"
    assert normalize_key("  Chrome-Free  Passivation ") == "chromefreepassivation"


def test_normalize_key_empty():
    assert normalize_key("") == ""


# ── store CRUD ───────────────────────────────────────────────────────────────


def test_upsert_then_match(store):
    store.upsert("key1", "产品A", _sys(system_name="体系A"))
    got = store.match("key1")
    assert got is not None
    assert got.system_name == "体系A"
    assert got.must_include_roles == ["resin"]
    assert got.metric_ranges["salt_spray_hours"] == (500.0, 1440.0)


def test_match_miss_returns_none(store):
    assert store.match("nonexistent") is None


def test_match_increments_hit_count(store):
    store.upsert("key2", "产品B", _sys())
    store.match("key2")
    store.match("key2")
    hot = store.hot(threshold=2)
    assert len(hot) == 1
    # upsert 首次沉淀 hit_count=1，两次 match 后 = 3（总使用次数）
    assert hot[0]["hit_count"] == 3


def test_upsert_is_idempotent(store):
    store.upsert("key3", "产品C", _sys(system_name="v1"))
    store.upsert("key3", "产品C", _sys(system_name="v2"))
    got = store.match("key3")
    assert got.system_name == "v2"
    # 单一命中即可证明未产生重复行（UNIQUE 键下重复 upsert 会覆盖而非新增）
    hot = store.hot(threshold=1)
    assert len(hot) == 1


def test_hot_respects_threshold(store):
    store.upsert("k_a", "a", _sys())
    store.upsert("k_b", "b", _sys())
    for _ in range(5):
        store.match("k_a")
    hot = store.hot(threshold=5)
    assert [h["normalized_key"] for h in hot] == ["k_a"]


# ── 3-tier resolve ───────────────────────────────────────────────────────────


def test_resolve_static_hit(monkeypatch):
    import app.services.llm as llm_mod
    from app.domain.schemas import ProductDomain, Requirement

    req = Requirement(domain=ProductDomain.surface_treatment, product_type="自沉积型涂料")
    block = llm_mod._resolve_system_constraints(req)
    assert "Formulation-system requirements" in block
    assert "Autodeposition" in block


def test_resolve_cache_hit(store, monkeypatch):
    import app.services.llm as llm_mod
    from app.domain.formulation_systems import normalize_key
    from app.domain.schemas import ProductDomain, Requirement

    pt = "电子级环氧胶粘剂"
    store.upsert(normalize_key(pt), pt, _sys(system_name="电子胶粘剂"))

    req = Requirement(domain=ProductDomain.surface_treatment, product_type=pt)
    block = llm_mod._resolve_system_constraints(req)
    assert "self-learned" in block
    assert "电子胶粘剂" in block


def test_resolve_infer_and_persist(store, monkeypatch):
    import app.services.llm as llm_mod
    from app.domain.formulation_systems import normalize_key
    from app.domain.schemas import ProductDomain, Requirement

    monkeypatch.setattr(
        llm_mod, "_infer_system_constraints", lambda pt: _sys(system_name="水基切削液")
    )

    req = Requirement(domain=ProductDomain.degreaser, product_type="水基切削液")
    block = llm_mod._resolve_system_constraints(req)
    assert "self-learned" in block
    assert "水基切削液" in block

    # 已沉淀，可再次命中
    got = store.match(normalize_key("水基切削液"))
    assert got is not None
    assert got.system_name == "水基切削液"


def test_resolve_infer_failure_falls_back_to_infer_block(store, monkeypatch):
    import app.services.llm as llm_mod
    from app.domain.schemas import ProductDomain, Requirement

    monkeypatch.setattr(llm_mod, "_infer_system_constraints", lambda pt: None)

    req = Requirement(domain=ProductDomain.degreaser, product_type="未知体系XYZ")
    block = llm_mod._resolve_system_constraints(req)
    assert "INFER" in block
