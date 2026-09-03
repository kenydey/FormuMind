from fastapi.testclient import TestClient

from app.domain.schemas import ProductDomain, Requirement
from app.main import app
from app.pipeline import workflow
from app.services import io_export

client = TestClient(app)


def test_plan_to_csv_has_headers_and_blank_measured_column():
    req = Requirement(domain=ProductDomain.anticorrosion_coating, cure_temperature_c=80)
    plan = workflow.build_doe(req, design="full_factorial")
    csv_text = io_export.plan_to_csv(plan, ["salt_spray_hours"])
    lines = csv_text.strip().splitlines()
    header = lines[0].split(",")
    assert "run_id" in header
    assert "domain" in header
    assert "measured_salt_spray_hours" in header
    # As many data rows as runs, and the measured column is blank.
    assert len(lines) - 1 == len(plan.runs)
    assert lines[1].rstrip(",").endswith("")  # trailing blank measured cell


def test_csv_round_trip_to_records():
    req = Requirement(domain=ProductDomain.anticorrosion_coating, cure_temperature_c=80)
    plan = workflow.build_doe(req, design="plackett_burman")
    csv_text = io_export.plan_to_csv(plan, ["salt_spray_hours"])

    # Simulate the lab filling in every measured cell.
    lines = csv_text.strip().splitlines()
    header = lines[0].split(",")
    midx = header.index("measured_salt_spray_hours")
    filled = [lines[0]]
    for i, line in enumerate(lines[1:]):
        cells = line.split(",")
        cells[midx] = str(900 + i * 10)
        filled.append(",".join(cells))
    filled_csv = "\n".join(filled)

    records = io_export.csv_to_records(filled_csv)
    assert len(records) == len(plan.runs)
    for rec in records:
        assert rec.domain == ProductDomain.anticorrosion_coating
        assert "salt_spray_hours" in rec.measured
        # cure_temperature_c is a factor in the plan and routed to the process field.
        assert rec.cure_temperature_c is not None
        assert "cure_temperature_c" not in rec.factors


def test_csv_skips_unfilled_rows():
    csv_text = (
        "run_id,domain,Zinc phosphate,measured_salt_spray_hours\n"
        "1,anticorrosion_coating,12,980\n"
        "2,anticorrosion_coating,8,\n"  # unfilled -> skipped
    )
    records = io_export.csv_to_records(csv_text)
    assert len(records) == 1
    assert records[0].measured["salt_spray_hours"] == 980.0
    assert records[0].factors["Zinc phosphate"] == 12.0


def test_csv_default_domain_fallback():
    csv_text = "run_id,Zinc phosphate,measured_salt_spray_hours\n1,12,980\n"
    records = io_export.csv_to_records(csv_text, default_domain=ProductDomain.anticorrosion_coating)
    assert len(records) == 1
    assert records[0].domain == ProductDomain.anticorrosion_coating


def test_export_endpoint_returns_csv_attachment():
    req = {"domain": "anticorrosion_coating", "cure_temperature_c": 90}
    plan = client.post("/api/doe?design=plackett_burman", json=req).json()
    plan_id = plan["plan_id"]
    assert plan_id

    r = client.get(f"/api/doe/{plan_id}/export?format=csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert "measured_salt_spray_hours" in r.text


def test_export_unknown_plan_404():
    r = client.get("/api/doe/does-not-exist/export?format=csv")
    assert r.status_code == 404


def test_import_csv_endpoint_trains_model():
    from app.services.training import registry

    registry.reset(persist=True)
    try:
        rows = ["run_id,domain,Zinc phosphate,cure_temperature_c,measured_salt_spray_hours"]
        for i, z in enumerate([3, 5, 7, 9, 11, 13]):
            rows.append(f"{i + 1},anticorrosion_coating,{z},80,{200 + 80 * z}")
        csv_bytes = ("\n".join(rows)).encode("utf-8")

        r = client.post(
            "/api/experiments/import-csv",
            files={"file": ("results.csv", csv_bytes, "text/csv")},
        )
        assert r.status_code == 200
        report = r.json()
        assert report["total_records"] == 6
        assert any(m["metric"] == "salt_spray_hours" for m in report["trained"])
    finally:
        registry.reset(persist=True)


def test_import_csv_empty_rows_422():
    csv_bytes = b"run_id,domain,Zinc phosphate,measured_salt_spray_hours\n1,anticorrosion_coating,12,\n"
    r = client.post(
        "/api/experiments/import-csv",
        files={"file": ("empty.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 422
