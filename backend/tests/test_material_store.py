"""Material space: store, catalog proxy, and API.

The catalog proxy replaces a module-level dict literal that ~20 call sites read,
iterate, or membership-test — and that 8 modules bind at import time. The tests
below pin the four behaviours those call sites depend on (order, live
references, blank-only merging, degrade-to-seed) rather than just CRUD.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.database import Base, make_engine, make_session_factory
from app.db.material_store import MaterialStore, norm_key
from app.domain import knowledge
from app.domain.material_catalog import MaterialCatalog, candidates_by_role
from app.main import app

client = TestClient(app)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A MaterialStore on an isolated DB, installed as the global singleton."""
    import app.db.material_store as material_store_mod

    engine = make_engine(f"sqlite:///{tmp_path}/materials.db")
    Base.metadata.create_all(engine)
    st = MaterialStore(make_session_factory(engine))
    monkeypatch.setattr(material_store_mod, "_store", st)
    knowledge.RAW_MATERIALS.refresh()
    yield st
    knowledge.RAW_MATERIALS.refresh()


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ── norm_key ─────────────────────────────────────────────────────────────────


def test_norm_key_collapses_separators_and_case():
    assert norm_key("Desmodur BL 3175") == norm_key("desmodur-bl_3175")
    assert norm_key("Bisphenol-A epoxy (DGEBA)") == "bisphenolaepoxydgeba"


def test_norm_key_handles_empty():
    assert norm_key("") == ""


# ── store ────────────────────────────────────────────────────────────────────


def test_seed_missing_is_idempotent(store):
    first = store.seed_missing(knowledge._SEED_MATERIALS)
    assert first == len(knowledge._SEED_MATERIALS)
    assert store.seed_missing(knowledge._SEED_MATERIALS) == 0
    assert store.count() == len(knowledge._SEED_MATERIALS)


def test_seed_missing_preserves_user_edits(store):
    store.seed_missing(knowledge._SEED_MATERIALS)
    store.upsert("Xylene", {"price_cny_per_kg": 99.0}, overwrite=True)
    # Re-seeding must not reset the edited price back to the literal's value.
    store.seed_missing(knowledge._SEED_MATERIALS)
    assert store.get("Xylene").price_cny_per_kg == 99.0


def test_upsert_blank_only_merge_protects_curated_values(store):
    store.upsert("Talc", {"role": "filler", "supplier": "ACME"}, overwrite=True)
    # A harvested row must never clobber a populated field.
    store.upsert("Talc", {"supplier": "OTHER", "hlb": 12.0}, overwrite=False)
    row = store.get("Talc")
    assert row.supplier == "ACME"
    assert row.hlb == 12.0  # blank field still gets filled


def test_row_to_spec_omits_nulls(store):
    """Absence differs from None: consumers read .get(key, default)."""
    store.upsert("Mystery solvent", {"role": "solvent"}, origin="user")
    spec = MaterialStore.row_to_spec(store.get("Mystery solvent"))
    assert spec["role"] == "solvent"
    assert "carrier" not in spec
    # This is the behaviour that would break if NULL became None:
    assert spec.get("carrier", "both") == "both"


def test_search_filters_by_role_and_availability(store):
    store.seed_missing(knowledge._SEED_MATERIALS)
    resins = store.search(role="resin", limit=50)
    assert resins and all(r.role == "resin" for r in resins)
    store.set_availability("Xylene", "discontinued")
    gone = store.search(availability="discontinued", limit=50)
    assert [r.name for r in gone] == ["Xylene"]


def test_generation_bumps_on_write(store):
    before = store.generation
    store.upsert("Xylene", {"role": "solvent"})
    assert store.generation > before


# ── catalog proxy ────────────────────────────────────────────────────────────


def test_catalog_degrades_to_seed_when_store_disabled(monkeypatch):
    monkeypatch.setenv("FORMUMIND_MATERIAL_STORE_ENABLED", "false")
    get_settings.cache_clear()
    catalog = MaterialCatalog(knowledge._SEED_MATERIALS)
    assert dict(catalog) == knowledge._SEED_MATERIALS
    assert list(catalog) == list(knowledge._SEED_MATERIALS)


def test_catalog_preserves_seed_order_with_additions_appended(store):
    """colbert_store slices [:40] and four lookups scan first-match-wins."""
    store.seed_missing(knowledge._SEED_MATERIALS)
    store.upsert("ZZZ Novel Additive", {"role": "additive"}, origin="user")
    knowledge.RAW_MATERIALS.refresh()
    names = list(knowledge.RAW_MATERIALS)
    seed_names = list(knowledge._SEED_MATERIALS)
    assert names[: len(seed_names)] == seed_names
    assert "ZZZ Novel Additive" in names[len(seed_names) :]


def test_catalog_hands_out_live_references(store):
    """enrich_materials() backfills by mutating the nested spec in place."""
    store.seed_missing(knowledge._SEED_MATERIALS)
    knowledge.RAW_MATERIALS.refresh()
    spec = knowledge.RAW_MATERIALS["Polyurethane dispersion"]
    spec["smiles"] = "CCO"
    assert knowledge.RAW_MATERIALS["Polyurethane dispersion"]["smiles"] == "CCO"
    # ...and the same object is handed out by items()
    from_items = dict(knowledge.RAW_MATERIALS.items())["Polyurethane dispersion"]
    assert from_items is spec


def test_catalog_supports_mapping_protocol(store):
    store.seed_missing(knowledge._SEED_MATERIALS)
    knowledge.RAW_MATERIALS.refresh()
    cat = knowledge.RAW_MATERIALS
    assert "Xylene" in cat
    assert cat.get("Xylene")["role"] == "solvent"
    assert cat.get("nope") is None
    assert len(cat) >= len(knowledge._SEED_MATERIALS)
    assert "Xylene" in list(cat.keys())


def test_catalog_db_row_fills_blank_seed_field_only(store):
    """A stored SMILES backfills a blank seed field but never overwrites one."""
    store.seed_missing(knowledge._SEED_MATERIALS)
    store.upsert("Polyurethane dispersion", {"smiles": "CCN"}, overwrite=True)
    store.upsert("Bisphenol-A epoxy (DGEBA)", {"smiles": "XXXX"}, overwrite=True)
    knowledge.RAW_MATERIALS.refresh()
    assert knowledge.RAW_MATERIALS["Polyurethane dispersion"]["smiles"] == "CCN"
    # DGEBA has a curated SMILES — the overlay must leave it alone.
    assert knowledge.RAW_MATERIALS["Bisphenol-A epoxy (DGEBA)"]["smiles"] != "XXXX"


def test_catalog_never_mutates_the_seed_literal(store):
    """The snapshot is writable, but writes must not reach the module literal.

    _SEED_MATERIALS is process-global; if the catalog handed out its dicts,
    every in-place write (enrich_materials does exactly this) would be
    permanent and un-refreshable, and would leak across callers.
    """
    store.seed_missing(knowledge._SEED_MATERIALS)
    knowledge.RAW_MATERIALS.refresh()
    knowledge.RAW_MATERIALS["Xylene"]["price_cny_per_kg"] = 999.0
    assert knowledge._SEED_MATERIALS["Xylene"]["price_cny_per_kg"] != 999.0
    # ...and a refresh drops the uncommitted edit rather than baking it in.
    knowledge.RAW_MATERIALS.refresh()
    assert knowledge.RAW_MATERIALS["Xylene"]["price_cny_per_kg"] != 999.0


def test_availability_toggles_both_ways(store):
    """Overlay values must not go sticky once merged onto a seed entry."""
    store.seed_missing(knowledge._SEED_MATERIALS)
    store.set_availability("Xylene", "discontinued")
    knowledge.RAW_MATERIALS.refresh()
    assert knowledge.RAW_MATERIALS["Xylene"]["availability"] == "discontinued"

    store.set_availability("Xylene", "in_stock")
    knowledge.RAW_MATERIALS.refresh()
    assert knowledge.RAW_MATERIALS["Xylene"]["availability"] == "in_stock"


def test_candidates_by_role_returns_catalog_order(store):
    store.seed_missing(knowledge._SEED_MATERIALS)
    knowledge.RAW_MATERIALS.refresh()
    hardeners = candidates_by_role(knowledge.RAW_MATERIALS, "hardener")
    assert "Polyamide hardener" in hardeners
    assert all(knowledge.RAW_MATERIALS[n]["role"] == "hardener" for n in hardeners)


# ── downstream consumers keep working ────────────────────────────────────────


def test_existing_consumers_still_resolve(store):
    """The catalog swap must be invisible to knowledge.py's own helpers."""
    store.seed_missing(knowledge._SEED_MATERIALS)
    knowledge.RAW_MATERIALS.refresh()
    assert knowledge.resolve_material_name("epon 828") == "Bisphenol-A epoxy (DGEBA)"
    assert knowledge.resolve_material_name("XYLENE") == "Xylene"
    ing = knowledge.ingredient("Zinc phosphate", 5.0)
    assert ing.role == "inhibitor" and ing.weight_pct == 5.0


def test_baseline_formulation_unchanged_by_catalog(store):
    from app.domain.schemas import ProductDomain, Requirement

    store.seed_missing(knowledge._SEED_MATERIALS)
    knowledge.RAW_MATERIALS.refresh()
    form = knowledge.baseline_formulation(
        Requirement(domain=ProductDomain.anticorrosion_coating)
    )
    assert form.ingredients
    assert abs(form.total_pct() - 100.0) < 0.01


# ── API ──────────────────────────────────────────────────────────────────────


def test_list_materials_returns_catalog():
    r = client.get("/api/materials?limit=500")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 1
    names = {m["name"] for m in body["materials"]}
    assert "Xylene" in names
    xylene = next(m for m in body["materials"] if m["name"] == "Xylene")
    assert xylene["role"] == "solvent"
    assert "price_cny_per_kg" in xylene["spec"]


def test_list_materials_filters_by_role():
    r = client.get("/api/materials?role=resin&limit=100")
    assert r.status_code == 200
    assert all(m["role"] == "resin" for m in r.json()["materials"])


def test_upsert_material_then_visible(store):
    r = client.post(
        "/api/materials",
        json={
            "name": "Test hardener X",
            "role": "hardener",
            "functional_class": "amine",
            "price_cny_per_kg": 42.0,
            "enrich": False,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "hardener"
    assert "Test hardener X" in knowledge.RAW_MATERIALS
    assert knowledge.RAW_MATERIALS["Test hardener X"]["functional_class"] == "amine"


def test_upsert_material_rejects_bad_availability(store):
    r = client.post(
        "/api/materials",
        json={"name": "Bad", "role": "solvent", "availability": "nonsense", "enrich": False},
    )
    assert r.status_code == 400


def test_set_availability_marks_discontinued(store):
    store.seed_missing(knowledge._SEED_MATERIALS)
    knowledge.RAW_MATERIALS.refresh()
    r = client.post(
        "/api/materials/availability",
        json={"name": "Xylene", "availability": "discontinued"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["availability"] == "discontinued"


def test_set_availability_unknown_material_404(store):
    r = client.post(
        "/api/materials/availability",
        json={"name": "Not A Material", "availability": "discontinued"},
    )
    assert r.status_code == 404
