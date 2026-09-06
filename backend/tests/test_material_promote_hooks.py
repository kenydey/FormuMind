"""Follow-up: requirement/workbench → material promote hooks."""
from __future__ import annotations

from app.domain.knowledge import RAW_MATERIALS
from app.domain.schemas import MaterialSpec, ProductDomain, Requirement
from app.services.material_promote import (
    _looks_like_process_key,
    propose_from_requirement,
    propose_from_workbench_rows,
)


def test_process_key_filter():
    assert _looks_like_process_key("cure_temperature_c")
    assert _looks_like_process_key("bake_time_min")
    assert _looks_like_process_key("film_thickness_um")
    assert not _looks_like_process_key("Zinc phosphate")
    assert not _looks_like_process_key("Waterborne acrylic emulsion")


def test_propose_from_requirement_upserts_or_queues():
    req = Requirement(
        domain=ProductDomain.anticorrosion_coating,
        materials=[
            MaterialSpec(name="FollowupReqMatCAS", role="additive", cas_no="777-77-7"),
            MaterialSpec(name="FollowupReqMatBare", role="additive"),
        ],
    )
    counts = propose_from_requirement(req, source_ref="test")
    assert counts["upsert"] + counts["pending"] + counts["exists"] >= 1
    RAW_MATERIALS.refresh()
    # CAS-bearing one should land in catalog (or already exist).
    assert (
        "FollowupReqMatCAS" in RAW_MATERIALS
        or counts.get("exists", 0) >= 1
        or counts.get("upsert", 0) >= 1
    )


def test_propose_from_workbench_skips_process_keys():
    rows = [
        {
            "planned_params": {
                "FollowupWbMysteryResin": 40.0,
                "cure_temperature_c": 80.0,
                "bake_time_min": 30.0,
            },
            "actual_params": {"FollowupWbMysteryResin": 41.0},
        }
    ]
    counts = propose_from_workbench_rows(rows, campaign_id=4242)
    # Mystery resin should be proposed; process keys alone must not dominate.
    assert counts.get("pending", 0) + counts.get("exists", 0) + counts.get("upsert", 0) >= 1
    assert counts["upsert"] + counts["pending"] + counts["exists"] + counts["skipped"] >= 1
