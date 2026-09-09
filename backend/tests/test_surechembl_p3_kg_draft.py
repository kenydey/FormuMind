"""SureChEMBL P3 — KG ingest + human-reviewed embodiment drafts."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.entity_store import EntityStore
from app.services import surechembl_client, surechembl_drafts, surechembl_kg


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP", "1")
    get_settings.cache_clear()
    surechembl_client.clear_surechembl_cache()
    yield
    get_settings.cache_clear()
    surechembl_client.clear_surechembl_cache()


@pytest.fixture()
def entity_store(tmp_path, monkeypatch):
    import app.db.entity_store as entity_store_mod

    engine = make_engine(f"sqlite:///{tmp_path}/surechembl_p3.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    store = EntityStore(factory)
    monkeypatch.setattr(entity_store_mod, "_store", store)
    return store


_CHEM_ROWS = [
    {
        "chemical_id": "1001",
        "name": "zinc phosphate",
        "smiles": "O=P([O-])([O-])[O-].[Zn+2]",
        "formula": "ZnH3O4P",
        "global_frequency": 42,
        "similarity": 0.91,
    },
    {
        "chemical_id": "1002",
        "name": "epoxy resin fragment",
        "smiles": "C1OC1c2ccccc2",
        "formula": "C8H8O",
        "global_frequency": 17,
    },
    {
        "chemical_id": "9",
        "name": "water",
        "smiles": "O",
        "global_frequency": 999,
    },
]


def test_ingest_document_graph_creates_entities_and_appears_in(entity_store, monkeypatch):
    monkeypatch.setattr(
        surechembl_client,
        "document_chemistry",
        lambda *a, **k: list(_CHEM_ROWS),
    )
    out = surechembl_kg.ingest_document_graph(
        doc_id="CN-104789083-B",
        title="Quick-drying zinc phosphate epoxy primer",
        assignee="ACME CO",
        pub_date="20170725",
        url="https://patents.google.com/patent/CN104789083B",
        fetch_chemistry=True,
    )
    assert out["ok"] is True
    assert out["link_type"] == "appears_in"
    patent = entity_store.get_entity("patent:scpn:CN-104789083-B")
    assert patent is not None
    assert patent.canonical_name.startswith("Quick-drying")
    assert patent.supplier == "ACME CO"
    assert patent.grade == "20170725"
    chem = entity_store.get_entity("chem:surechembl:1001")
    assert chem is not None
    assert chem.canonical_name == "zinc phosphate"
    links = entity_store.get_links_for_entity(
        "chem:surechembl:1001",
        link_types=["appears_in"],
    )
    assert any(l.dst_entity_id == "patent:scpn:CN-104789083-B" for l in links)
    meta = next(l for l in links if l.dst_entity_id == "patent:scpn:CN-104789083-B")
    assert meta.metadata_json.get("frequency") == 42
    assert meta.metadata_json.get("similarity") == 0.91
    assert out["links"] >= 2


def test_ingest_uses_claimed_in_when_section_is_claims(entity_store, monkeypatch):
    monkeypatch.setattr(surechembl_client, "document_chemistry", lambda *a, **k: [_CHEM_ROWS[0]])
    out = surechembl_kg.ingest_document_graph(
        doc_id="US-20120129964-A1",
        title="Claims example",
        chemicals=[_CHEM_ROWS[0]],
        fetch_chemistry=False,
        section="Claims",
    )
    assert out["link_type"] == "claimed_in"
    links = entity_store.get_links_for_entity(
        "chem:surechembl:1001",
        link_types=["claimed_in"],
    )
    assert links


def test_extract_example_draft_does_not_write_db(entity_store, monkeypatch):
    monkeypatch.setattr(
        surechembl_client,
        "document_chemistry",
        lambda *a, **k: list(_CHEM_ROWS),
    )
    before = entity_store.stats()
    out = surechembl_drafts.extract_example_draft(
        doc_id="CN-104789083-B",
        title="Primer",
        assignee="ACME",
    )
    assert out["ok"] is True
    draft = out["draft"]
    assert draft["origin"] == "surechembl"
    assert draft["needs_review"] is True
    assert draft["status"] == "draft"
    names = {i["name"].casefold() for i in draft["formulation"]["ingredients"]}
    assert "water" not in names
    assert "zinc phosphate" in names
    after = entity_store.stats()
    assert after == before


def test_confirm_example_draft_pending_only_no_pool(entity_store, monkeypatch):
    proposed: list[dict] = []

    def _fake_propose(name, spec=None, *, source="kb_promoted", source_ref="", force_pending=False):
        assert force_pending is True
        assert source == "surechembl"
        proposed.append({"name": name, "force_pending": force_pending})
        return {"action": "pending", "name": name}

    monkeypatch.setattr(surechembl_drafts, "propose_material", _fake_propose)
    monkeypatch.setattr(surechembl_client, "document_chemistry", lambda *a, **k: [])

    draft = {
        "status": "draft",
        "needs_review": True,
        "origin": "surechembl",
        "doc_id": "CN-104789083-B",
        "title": "Primer",
        "assignee": "ACME",
        "pub_date": "20170725",
        "url": "https://patents.google.com/patent/CN104789083B",
        "formulation": {
            "name": "SureChEMBL 草稿 · CN-104789083-B",
            "domain": "anticorrosion_coating",
            "ingredients": [
                {"name": "zinc phosphate", "role": "additive", "weight_pct": 50.0, "smiles": "O"}
            ],
            "warnings": [],
            "source": "surechembl",
        },
        "ingredients_detail": [
            {
                "name": "zinc phosphate",
                "role": "additive",
                "weight_pct": 50.0,
                "smiles": "O=P(O)(O)O.[Zn]",
                "chemical_id": "1001",
                "global_frequency": 42,
            }
        ],
    }
    out = surechembl_drafts.confirm_example_draft(draft)
    assert out["ok"] is True
    assert out["promoted_to_pool"] is False
    assert proposed and proposed[0]["force_pending"] is True
    assert entity_store.get_entity("patent:scpn:CN-104789083-B") is not None
    form_eid = out["formulation_entity_id"]
    assert form_eid.startswith("form:surechembl:")
    form_ent = entity_store.get_entity(form_eid)
    assert form_ent is not None
    assert "status:pending_review" in (form_ent.aliases or [])


def test_confirm_rejects_non_review_draft():
    out = surechembl_drafts.confirm_example_draft(
        {"origin": "surechembl", "needs_review": False, "doc_id": "X"}
    )
    assert out["ok"] is False
    assert out["reason"] == "draft_not_reviewable"


def test_api_routes(entity_store, monkeypatch):
    monkeypatch.setattr(
        surechembl_client,
        "document_chemistry",
        lambda *a, **k: list(_CHEM_ROWS),
    )
    proposed = []

    def _fake_propose(name, spec=None, *, source="kb_promoted", source_ref="", force_pending=False):
        proposed.append(name)
        return {"action": "pending", "name": name}

    monkeypatch.setattr(surechembl_drafts, "propose_material", _fake_propose)

    from app.main import app

    client = TestClient(app)
    ing = client.post(
        "/api/surechembl/kg/ingest-document",
        json={"doc_id": "CN-104789083-B", "title": "Primer", "fetch_chemistry": True},
    )
    assert ing.status_code == 200, ing.text
    assert ing.json()["entities"] >= 1

    ext = client.post(
        "/api/surechembl/extract-example-draft",
        json={"doc_id": "CN-104789083-B", "title": "Primer"},
    )
    assert ext.status_code == 200, ext.text
    draft = ext.json()["draft"]
    assert draft["needs_review"] is True

    conf = client.post("/api/surechembl/confirm-example-draft", json={"draft": draft})
    assert conf.status_code == 200, conf.text
    body = conf.json()
    assert body["promoted_to_pool"] is False
    assert proposed
