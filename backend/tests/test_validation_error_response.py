"""A field whose validator raises ``ValueError`` is a 422, not a 500 (found by the API fuzzer: an invalid objective ``direction``).

Pydantic keeps the raised exception in ``ctx["error"]``; the app's own 422 handler put it into a ``JSONResponse`` as it was and the
encoder raised ``TypeError`` - so eight endpoints that take objectives answered 500 to a client mistake.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import _json_safe_errors, app

OBJECTIVE = {"metric": "salt_spray_hours", "direction": "NaN"}
REQUIREMENT = {"domain": "anticorrosion_coating", "objectives": [OBJECTIVE]}


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/doe/suggest-factors", REQUIREMENT),
        ("/api/doe/active", REQUIREMENT),
        ("/api/loop/iterate", REQUIREMENT),
        ("/api/doe/cycle", {"requirement": REQUIREMENT}),
        ("/api/formulations/recommend", {"requirement": REQUIREMENT}),
        ("/api/design/inverse", {"requirement": REQUIREMENT}),
        ("/api/materials/promote-from-requirement", {"requirement": REQUIREMENT}),
    ],
)
def test_an_invalid_objective_direction_is_a_422_that_says_what_is_wrong(path, body):
    response = TestClient(app).post(path, json=body)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert any("invalid direction" in str(item["msg"]) for item in detail)


def test_the_exception_in_ctx_is_sent_as_its_message():
    error = {"type": "value_error", "loc": ["body", "x"], "msg": "Value error, bad", "ctx": {"error": ValueError("bad")}}
    assert _json_safe_errors([error])[0]["ctx"] == {"error": "bad"}
