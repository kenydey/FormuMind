"""Hostile-but-well-formed requests must not 500 (round-4 API fuzz).

A schema-guided fuzz — every documented operation, ~2,800 mutated requests per seed — found two classes:

* ids too large for the database (``/api/experiments/999999999999999999999999999999/attachments``): ``int`` is unbounded in
  Python and every id reaches SQLite as a parameter, which raises ``OverflowError`` above 2**63 — a 500 on 20 endpoints;
* ``NaN`` / ``Infinity`` in a numeric field (``objectives[].weight``, ``days``, ``step_idx`` …): ``json.loads`` accepts the
  literals and pydantic ``float`` accepts the values. 500s on seven endpoints, and a non-finite value that is stored
  makes every later response fail to serialise.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)
HEADERS = {"content-type": "application/json"}


# ── ids that cannot exist ────────────────────────────────────────────────────────

HUGE = "9" * 30


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", f"/api/experiments/workbench/{HUGE}"),
        ("get", f"/api/experiments/workbench/{HUGE}/rounds"),
        ("get", f"/api/experiments/workbench/{HUGE}/rows/{HUGE}/attachments"),
        ("get", f"/api/experiments/{HUGE}/attachments"),
        ("get", f"/api/qc/experiments/{HUGE}/measurements"),
        ("delete", f"/api/memories/{HUGE}"),
    ],
)
def test_an_id_too_large_for_the_database_is_not_found_not_a_server_error(method, path):
    response = getattr(client, method)(path)
    assert response.status_code == 404, (response.status_code, response.text[:120])
    assert response.json() == {"detail": "Not found"}


def test_an_id_at_the_edge_is_still_a_normal_lookup():
    assert client.get(f"/api/experiments/{2**63 - 1}/attachments").status_code != 500


def test_an_unrelated_overflow_is_still_a_server_error():
    """The handler only speaks for the SQLite id overflow; any other OverflowError stays visible as a defect."""
    import asyncio

    from app.main import integer_out_of_range_handler

    with pytest.raises(OverflowError):
        asyncio.run(integer_out_of_range_handler(None, OverflowError("math range error")))  # type: ignore[arg-type]


# ── non-finite numbers ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize(
    "path, template",
    [
        ("/api/kb/retention/purge", '{"days": %s}'),
        ("/api/doe/suggest-factors", '{"requirement": {"domain": "anticorrosion_coating", "objectives": [{"metric": "adhesion_mpa", "direction": "maximize", "weight": %s}]}}'),
        ("/api/formulations/manual", '{"ingredients": [{"name": "x", "weight_pct": %s}]}'),
    ],
)
def test_non_finite_numbers_in_a_request_body_are_a_422(path, template, literal):
    response = client.post(path, content=(template % literal).encode(), headers=HEADERS)
    assert response.status_code == 422, (path, literal, response.status_code, response.text[:160])


def test_ordinary_bodies_are_unaffected():
    response = client.post("/api/formulations/manual", json={"ingredients": [{"name": "epoxy", "weight_pct": 40.5}]})
    assert response.status_code < 500


def test_the_strict_parser_is_installed_once_and_matches_json_for_normal_input():
    import asyncio

    from starlette.requests import Request

    from app import strict_json

    strict_json.install()
    strict_json.install()
    assert Request.json is strict_json._strict_json

    async def parse(raw: bytes):
        async def receive():
            return {"type": "http.request", "body": raw, "more_body": False}

        req = Request({"type": "http", "method": "POST", "headers": [], "query_string": b""}, receive)
        return await req.json()

    assert asyncio.run(parse(b'{"a": [1, 2.5, "x", null, true]}')) == {"a": [1, 2.5, "x", None, True]}
    # only a bare number token is refused: text that merely says NaN is data
    assert asyncio.run(parse('{"note": "result: NaN, Infinity", "k": [-1e3]}'.encode())) == {
        "note": "result: NaN, Infinity",
        "k": [-1000.0],
    }
    for bare in (b'{"a": NaN}', b'{"a": [1, Infinity]}', b'{"a": -Infinity}'):
        with pytest.raises(json.JSONDecodeError):
            asyncio.run(parse(bare))
