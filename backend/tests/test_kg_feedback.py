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


def test_measured_evidence_noop_when_domain_missing(entity_store, monkeypatch):
    _upsert(entity_store, id="met1", canonical_name="salt_spray_resistance", kind="property")
    rows = [
        WorkbenchRow(
            id=3, campaign_id=11, item_id="s3",
            measurements={"salt_spray_resistance": 900},
        )
    ]
    fake = _FakeCampaignStore("anticorrosion_coating", rows)
    monkeypatch.setattr(kg_feedback, "get_campaign_store", lambda: fake)

    assert kg_feedback.ingest_measured_evidence(11) == 0
