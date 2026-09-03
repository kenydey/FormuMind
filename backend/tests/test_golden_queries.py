"""Golden-query regression — retrieval relevance + filter_report contract."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain.schemas import Evidence
from app.services import literature

_FIXTURE = Path(__file__).parent / "fixtures" / "golden_queries.json"
_REQUIREMENT = {
    "domain": "anticorrosion_coating",
    "substrate": "carbon_steel",
    "salt_spray_hours": 500,
    "film_weight_gsm": 70,
    "cure_temperature_c": 80,
    "cleaning_efficiency": 90,
    "voc_limit_gpl": 420,
    "ph_target": None,
    "notes": "",
    "objectives": [],
}


def _load_cases():
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id"])
def test_golden_query(case):
    req = literature.Requirement(**_REQUIREMENT)

    if case.get("use_merge_only"):
        rows = [
            Evidence(**row)
            for row in case["inline_evidence"]
        ]
        kept, report = literature._merge_filter_rank(rows, case["query"], 20)
        filter_report = report.as_dict()
    else:
        kept, filter_report = literature.iter_search(
            case["query"],
            case["source_types"],
            req=req,
            total_limit=300,
        )

    ids = {e.identifier for e in kept}
    for expected in case.get("expect_ids", []):
        assert expected in ids, f"missing expected id {expected!r} in {ids}"
    for forbidden in case.get("must_not_include", []):
        assert forbidden not in ids, f"forbidden id {forbidden!r} present in {ids}"

    assert filter_report["kept"] >= case.get("min_kept", 1)
    assert isinstance(filter_report.get("dropped_by_reason"), dict)
    for reason in case.get("expect_drop_reasons", []):
        assert filter_report["dropped_by_reason"].get(reason, 0) >= 1
