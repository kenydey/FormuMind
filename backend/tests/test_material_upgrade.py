"""Material import/export + promote pipeline tests."""
from __future__ import annotations

import io
import json

from fastapi.testclient import TestClient

from app.domain.knowledge import RAW_MATERIALS
from app.main import app
from app.services import material_io
from app.services.grounded_recommend import ground_recommended_formulas
from app.services.material_promote import propose_material
from app.domain.schemas import ProductDomain, RecommendedFormula, RecommendedFormulaComponent

client = TestClient(app)


def test_csv_roundtrip_import_commit_and_export():
    unique = "P8ResinZ9q1"
    csv_body = (
        "name,role,cas_no,availability\n"
        f"{unique},resin,999-11-1,in_stock\n"
        "P8FillerZ9q2,filler,,in_stock\n"
    ).encode("utf-8")
    records = material_io.detect_and_parse("batch.csv", csv_body)
    assert len(records) == 2
    preview = material_io.commit_import(records, origin="import")
    assert preview.errors == 0
    assert preview.creates + preview.updates == 2
    RAW_MATERIALS.refresh()
    assert unique in RAW_MATERIALS

    exported = material_io.export_records(q=unique)
    assert any(r["name"] == unique for r in exported)
    # Idempotent re-import
    again = material_io.commit_import(records, origin="import")
    assert again.creates == 0
    assert again.updates >= 1


def test_import_api_dry_run_and_commit():
    payload = json.dumps(
        [
            {
                "name": "P8 API Import Material",
                "role": "solvent",
                "cas_no": "64-17-5",
                "availability": "in_stock",
            }
        ]
    ).encode("utf-8")
    files = {"file": ("mats.json", io.BytesIO(payload), "application/json")}
    r = client.post("/api/materials/import?dry_run=true", files=files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["total"] == 1

    files = {"file": ("mats.json", io.BytesIO(payload), "application/json")}
    r2 = client.post("/api/materials/import?dry_run=false", files=files)
    assert r2.status_code == 200, r2.text
    assert r2.json()["creates"] + r2.json()["updates"] >= 1


def test_export_api_csv():
    r = client.get("/api/materials/export?format=csv")
    assert r.status_code == 200
    assert "name" in r.text.splitlines()[0]
    assert "text/csv" in r.headers.get("content-type", "")


def test_propose_high_confidence_upserts_low_goes_pending():
    high = propose_material(
        "P8 High Conf Chem",
        {"role": "additive", "cas_no": "123-45-6"},
        source="kb_promoted",
    )
    assert high["action"] in {"upsert", "exists"}
    low = propose_material(
        "P8 Mystery Trade Name XYZ",
        {"role": "additive"},
        source="kb_promoted",
    )
    assert low["action"] in {"pending", "exists", "skipped"}
    if low["action"] == "pending":
        cand = client.get("/api/materials/candidates")
        assert cand.status_code == 200
        names = [c["name"] for c in cand.json()["candidates"]]
        assert "P8 Mystery Trade Name XYZ" in names


def test_prefer_materials_catalog_soft_sort_keeps_out_of_catalog():
    formulas = [
        RecommendedFormula(
            name="Outside Heavy",
            domain=ProductDomain.anticorrosion_coating,
            components=[
                RecommendedFormulaComponent(name="TotallyUnknownium-42", weight_pct=50.0),
            ],
        ),
        RecommendedFormula(
            name="Catalog Heavy",
            domain=ProductDomain.anticorrosion_coating,
            components=[
                RecommendedFormulaComponent(name="Bisphenol-A epoxy (DGEBA)", weight_pct=50.0),
            ],
        ),
    ]
    out, warnings = ground_recommended_formulas(
        formulas, [], prefer_materials_catalog=True
    )
    assert len(out) == 2
    assert out[0].name == "Catalog Heavy"
    assert any(c.name == "TotallyUnknownium-42" for c in out[1].components)
    assert any("软排序" in w or "优先材料库" in w for w in warnings)
    # No materials_only semantics: outsider still present.
    assert all(rec.name != "" for rec in out)


def test_recommend_request_accepts_prefer_flag_rejects_nothing():
    # Schema smoke: unknown materials_only must not be required; prefer is optional.
    from app.api.formulations import RecommendFormulationsRequest
    from app.domain.schemas import Requirement

    body = RecommendFormulationsRequest(
        requirement=Requirement(domain=ProductDomain.anticorrosion_coating),
        prefer_materials_catalog=True,
    )
    assert body.prefer_materials_catalog is True
    assert not hasattr(body, "materials_only")
