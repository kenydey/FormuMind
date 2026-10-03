"""Settings toggles that used to be accepted but never read (round-3 audit).

* ``auto_retrain`` ("实验自动重训") — ``ExperimentRegistry.add`` retrained on every
  submission no matter what the toggle said.
* ``kg_link_on_ingest`` — documented compat alias for the split
  ``kg_entities_on_ingest`` / ``kg_relations_on_ingest`` flags; ignored.
* The KG "link source" / "rebuild" actions piggy-backed on the *ingest-time*
  flags, so turning the (slow) ingest pass off made an explicit rebuild a silent
  no-op that still reported ``linked_sources = N``.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.config import get_settings
from app.db.chunk_store import ChunkStore
from app.db.database import Base, make_engine, make_session_factory
from app.db.entity_store import EntityStore
from app.db.source_store import SourceStore
from app.domain.schemas import ExperimentRecord, ProductDomain
from app.services import kb_index
from app.services.kg.entity_linker import link_source, rebuild_all
from app.services.training import ModelRegistry

MD = """# 防腐专利

## 实施例 1

环氧树脂 E51 一百质量份，磷酸锌防锈颜料十五份 CAS 7779-90-0，盐雾试验七百二十小时。
牌号 Heliogen L 936 蓝色颜料 2 份。
"""


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_KG_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_KB_V2_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_MODEL_ARTIFACTS_DIR", str(tmp_path / "model_artifacts"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set(monkeypatch, **flags: str) -> None:
    for key, value in flags.items():
        monkeypatch.setenv(f"FORMUMIND_{key.upper()}", value)
    get_settings.cache_clear()


# ── auto_retrain ────────────────────────────────────────────────────────────


def _records(n: int = 10) -> list[ExperimentRecord]:
    rng = np.random.default_rng(0)
    out = []
    for _ in range(n):
        zinc = float(rng.uniform(2.0, 14.0))
        out.append(
            ExperimentRecord(
                domain=ProductDomain.anticorrosion_coating,
                factors={"Zinc phosphate": zinc, "Bisphenol-A epoxy (DGEBA)": 38.0},
                cure_temperature_c=80.0,
                measured={"salt_spray_hours": 200.0 + 80.0 * zinc},
            )
        )
    return out


def test_add_retrains_by_default(tmp_path):
    reg = ModelRegistry(path=str(tmp_path / "exp.json"))
    assert reg.add(_records()) is True
    assert reg.info(), "default auto_retrain=True must train"


def test_auto_retrain_off_vetoes_retrain_but_still_stores(tmp_path, monkeypatch):
    _set(monkeypatch, auto_retrain="false")
    reg = ModelRegistry(path=str(tmp_path / "exp.json"))
    assert reg.add(_records()) is False
    assert reg.total_records == 10, "records are persisted regardless"
    assert reg.info() == [], "…but models are not refreshed"

    # An explicit train (POST /api/train) is never vetoed.
    assert reg.train()
    assert reg.info()


def test_explicit_retrain_false_is_still_honoured(tmp_path):
    reg = ModelRegistry(path=str(tmp_path / "exp.json"))
    assert reg.add(_records(), retrain=False) is False
    assert reg.info() == []


def test_submit_endpoint_explains_a_vetoed_retrain(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import training

    _set(monkeypatch, auto_retrain="false")
    reg = ModelRegistry(path=str(tmp_path / "exp.json"))
    monkeypatch.setattr(training, "registry", reg)
    import app.api.experiments as exp_api

    monkeypatch.setattr(exp_api, "registry", reg)

    body = {"records": [r.model_dump(mode="json") for r in _records(6)], "retrain": True}
    res = TestClient(app).post("/api/experiments", json=body)
    assert res.status_code == 200, res.text
    msg = res.json()["message"]
    assert "Auto-retrain is off" in msg and "/api/train" in msg
    assert res.json()["trained"] == []

    # With the toggle on the note is absent and models are trained.
    _set(monkeypatch, auto_retrain="true")
    res = TestClient(app).post("/api/experiments", json=body)
    assert "Auto-retrain is off" not in res.json()["message"]
    assert res.json()["trained"]


# ── kg_link_on_ingest + on-demand linking ───────────────────────────────────


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    import app.db.chunk_store as chunk_store_mod
    import app.db.entity_store as entity_store_mod
    import app.db.source_store as source_store_mod

    engine = make_engine(f"sqlite:///{tmp_path}/kg_flags.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    src, chk, ent = SourceStore(factory), ChunkStore(factory), EntityStore(factory)
    monkeypatch.setattr(source_store_mod, "_store", src)
    monkeypatch.setattr(chunk_store_mod, "_store", chk)
    monkeypatch.setattr(entity_store_mod, "_store", ent)
    return src, chk, ent


def _indexed_source(src, name: str) -> str:
    # Distinct text per source: identical chunks would be dropped by L1 dedup.
    text = MD + f"\n实施例 {name} 的对照样品另行记录。\n"
    sid = src.create(
        filename=name, title=name, source_kind="patent", full_text=text, content_hash=name
    )
    assert kb_index.index_source(sid, text, embed=False) >= 1
    return sid


def test_ingest_links_entities_by_default(stores):
    src, _, ent = stores
    _indexed_source(src, "on.md")
    assert ent.stats()["mentions"] >= 2


def test_legacy_master_switch_disables_ingest_linking(stores, monkeypatch):
    _set(monkeypatch, kg_link_on_ingest="false")
    src, _, ent = stores
    _indexed_source(src, "off.md")
    assert ent.stats()["mentions"] == 0, "kg_link_on_ingest=false must veto the ingest hook"


def test_split_flags_off_also_disable_ingest_linking(stores, monkeypatch):
    _set(monkeypatch, kg_entities_on_ingest="false", kg_relations_on_ingest="false")
    src, _, ent = stores
    _indexed_source(src, "split.md")
    assert ent.stats()["mentions"] == 0


def test_on_demand_link_ignores_ingest_time_flags(stores, monkeypatch):
    _set(monkeypatch, kg_entities_on_ingest="false", kg_relations_on_ingest="false")
    src, _, ent = stores
    sid = _indexed_source(src, "manual.md")
    assert ent.stats()["mentions"] == 0

    # Plain call keeps the flag semantics (what the ingest hook relies on)…
    assert link_source(sid).mentions_upserted == 0
    # …an explicit on-demand link does the work.
    report = link_source(sid, force_entities=True)
    assert report.mentions_upserted >= 2
    assert ent.stats()["mentions"] >= 2


def test_rebuild_all_actually_rebuilds_with_ingest_flags_off(stores, monkeypatch):
    _set(monkeypatch, kg_entities_on_ingest="false", kg_relations_on_ingest="false")
    src, _, ent = stores
    _indexed_source(src, "a.md")
    _indexed_source(src, "b.md")
    assert ent.stats()["mentions"] == 0

    report = rebuild_all()

    assert report.linked_sources == 2
    assert report.mentions_upserted >= 4, report
    assert ent.stats()["mentions"] >= 4


def test_rebuild_all_survives_a_failing_source_and_reports_it(stores, monkeypatch):
    """Latent bug found here: KGRebuildReport lacked ``relations_upserted``, so the
    loop died with AttributeError after the FIRST source and the broad except
    returned a partial report that looked like success."""
    from app.services.kg import entity_linker

    src, _, ent = stores
    ids = [_indexed_source(src, n) for n in ("x.md", "y.md", "z.md")]
    real = entity_linker.link_source

    def flaky(sid, **kw):
        if sid == ids[1]:
            raise RuntimeError("boom")
        return real(sid, **kw)

    monkeypatch.setattr(entity_linker, "link_source", flaky)
    report = rebuild_all()

    assert (report.linked_sources, report.failed_sources) == (2, 1)
    assert report.mentions_upserted >= 4
    assert report.relations_upserted == 0  # field exists and aggregates


def test_link_source_endpoint_is_on_demand(stores, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    _set(monkeypatch, kg_entities_on_ingest="false", kg_relations_on_ingest="false")
    src, _, ent = stores
    sid = _indexed_source(src, "api.md")

    res = TestClient(app).post(f"/api/kg/link-source/{sid}")
    assert res.status_code == 200, res.text
    assert res.json()["mentions_upserted"] >= 2
    assert ent.stats()["mentions"] >= 2
