"""P0 KG self-evolution: measured evidence writes back into the KG.

Verifies that ``services/kg_feedback.ingest_measured_evidence`` resolves the
campaign domain + measured metrics to KG entities and writes
``measured_performance`` links tagged ``extraction_method="measured"``, without
clobbering existing literature evidence (merge_semantic_link accumulates refs).
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db.entity_store import EntityStore
from app.services import kg_feedback
from app.db.campaign_types import WorkbenchRow


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def entity_store(tmp_path, monkeypatch):
    from app.db.database import Base, make_engine, make_session_factory
    import app.db.entity_store as es_mod

    engine = make_engine(f"sqlite:///{tmp_path}/kg.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    store = EntityStore(factory)
    monkeypatch.setattr(es_mod, "_store", store)
    return store


class _FakeCampaign:
    def __init__(self, domain: str):
        self.domain = domain


class _FakeCampaignStore:
    def __init__(self, domain: str, rows):
        self._campaign = _FakeCampaign(domain)
        self._rows = rows

    def get_campaign_sync(self, cid):
        return self._campaign

    def list_rows_sync(self, cid):
        return self._rows


def _upsert(store, **fields):
    with store._session_factory() as s:
        store.upsert_entity(s, **fields)


def _merge_link(store, **kw):
    with store._session_factory() as s:
        store.merge_semantic_link(s, **kw)


def test_measured_evidence_writes_kg_links(entity_store, monkeypatch):
    _upsert(entity_store, id="dom1", canonical_name="anticorrosion_coating", kind="domain")
    _upsert(entity_store, id="met1", canonical_name="salt_spray_resistance", kind="property")
    _upsert(entity_store, id="met2", canonical_name="cost_cny_per_kg", kind="property")

    rows = [
        WorkbenchRow(
            id=1,
            campaign_id=7,
            item_id="s1",
            measurements={"salt_spray_resistance": 1200, "cost_cny_per_kg": 28.5},
        )
    ]
    fake = _FakeCampaignStore("anticorrosion_coating", rows)
    monkeypatch.setattr(kg_feedback, "get_campaign_store", lambda: fake)

    written = kg_feedback.ingest_measured_evidence(7)
    assert written == 2, f"expected 2 measured links, got {written}"

    from app.db.models import KGEntityLink

    with entity_store._session_factory() as s:
        links = (
            s.query(KGEntityLink)
            .filter(KGEntityLink.link_type == "measured_performance")
            .all()
        )
        assert len(links) == 2
        for lnk in links:
            refs = lnk.evidence_refs or []
            assert any(r.get("extraction_method") == "measured" for r in refs)


def test_measured_evidence_accumulates_not_overwrites(entity_store, monkeypatch):
    _upsert(entity_store, id="dom1", canonical_name="anticorrosion_coating", kind="domain")
    _upsert(entity_store, id="met1", canonical_name="salt_spray_resistance", kind="property")
    _upsert(entity_store, id="met2", canonical_name="cost_cny_per_kg", kind="property")

    _merge_link(
        entity_store,
        src_entity_id="dom1",
        dst_entity_id="met1",
        link_type="measured_performance",
        confidence=0.5,
        evidence_ref={"source_id": "lit-1", "extraction_method": "rule",
                      "sentence": "literature says high"},
        extraction_method="rule",
    )

    rows = [
        WorkbenchRow(
            id=2,
            campaign_id=9,
            item_id="s2",
            measurements={"salt_spray_resistance": 1500},
        )
    ]
    fake = _FakeCampaignStore("anticorrosion_coating", rows)
    monkeypatch.setattr(kg_feedback, "get_campaign_store", lambda: fake)

    kg_feedback.ingest_measured_evidence(9)

    from app.db.models import KGEntityLink

    with entity_store._session_factory() as s:
        link = (
            s.query(KGEntityLink)
            .filter(KGEntityLink.link_type == "measured_performance")
            .first()
        )
        methods = {r.get("extraction_method") for r in (link.evidence_refs or [])}
        assert "measured" in methods
        assert "rule" in methods, "literature evidence must be preserved"


def test_measured_evidence_noop_when_domain_and_materials_missing(entity_store, monkeypatch):
    _upsert(entity_store, id="met1", canonical_name="salt_spray_resistance", kind="property")
    rows = [
        WorkbenchRow(
            id=3, campaign_id=11, item_id="s3",
            measurements={"salt_spray_resistance": 900},
            # no actual/planned params → no materials
        )
    ]
    fake = _FakeCampaignStore("anticorrosion_coating", rows)
    monkeypatch.setattr(kg_feedback, "get_campaign_store", lambda: fake)

    assert kg_feedback.ingest_measured_evidence(11) == 0


def test_measured_evidence_writes_material_links(entity_store, monkeypatch):
    """Material → property edges from actual_params (core 2026-09-16 MVP)."""
    _upsert(entity_store, id="dom1", canonical_name="anticorrosion_coating", kind="domain")
    _upsert(entity_store, id="met1", canonical_name="salt_spray_hours", kind="property")

    rows = [
        WorkbenchRow(
            id=4,
            campaign_id=21,
            item_id="s4",
            actual_params={"Zinc phosphate": 9.0, "cure_temperature_c": 82.0},
            measurements={"salt_spray_hours": 780.0},
        )
    ]
    fake = _FakeCampaignStore("anticorrosion_coating", rows)
    monkeypatch.setattr(kg_feedback, "get_campaign_store", lambda: fake)

    written = kg_feedback.ingest_measured_evidence(21)
    # 1 material (Zinc phosphate; cure temp skipped) × 1 metric + 1 domain × 1 metric
    assert written == 2, f"expected material+domain links, got {written}"

    from app.db.models import KGEntity, KGEntityLink

    with entity_store._session_factory() as s:
        mats = s.query(KGEntity).filter(KGEntity.id.like("mat:%")).all()
        assert len(mats) == 1
        assert "zinc" in mats[0].canonical_name.lower() or "Zinc" in mats[0].canonical_name
        mat_links = (
            s.query(KGEntityLink)
            .filter(
                KGEntityLink.src_entity_id == mats[0].id,
                KGEntityLink.link_type == "measured_performance",
            )
            .all()
        )
        assert len(mat_links) == 1
        refs = mat_links[0].evidence_refs or []
        assert any(r.get("granularity") == "material" for r in refs)
        assert any("Zinc phosphate" in (r.get("sentence") or "") for r in refs)


def test_measured_evidence_material_without_domain(entity_store, monkeypatch):
    """Domain missing must not block material-level flywheel."""
    _upsert(entity_store, id="met1", canonical_name="salt_spray_hours", kind="property")
    rows = [
        WorkbenchRow(
            id=5,
            campaign_id=22,
            item_id="s5",
            actual_params={"Epoxy resin": 40.0},
            measurements={"salt_spray_hours": 500.0},
        )
    ]
    fake = _FakeCampaignStore("unknown_domain_xyz", rows)
    monkeypatch.setattr(kg_feedback, "get_campaign_store", lambda: fake)

    written = kg_feedback.ingest_measured_evidence(22)
    assert written >= 1

    from app.db.models import KGEntityLink

    with entity_store._session_factory() as s:
        links = s.query(KGEntityLink).filter(KGEntityLink.src_entity_id.like("mat:%")).all()
        assert len(links) >= 1
