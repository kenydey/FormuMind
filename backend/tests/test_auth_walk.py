"""With API auth on, every documented operation but the public ones answers 401 without a token (round-4).

``ApiAuthMiddleware`` is one global gate, so no route can forget to opt in — but the gate has an allow-list
(``_PUBLIC_EXACT`` / ``_PUBLIC_PREFIXES``) and a query-string token carve-out for the SSE stream, and a prefix added to the
first or a loosened path test in the second would expose routes silently. This walks all of OpenAPI with no token, and
with a wrong one, and pins the exact set of operations that stay open.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.middleware.api_auth import reset_dev_token_cache

TOKEN = "walk-secret-token"
# The operations that must stay reachable without credentials: liveness, the API docs, and the auth probe the UI calls
# before it has a token. /health/detailed (infrastructure details) is deliberately not among them.
PUBLIC = {
    ("GET", "/health"),
    ("GET", "/api/auth/status"),
}
DOCS = ("/docs", "/redoc", "/openapi.json")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_API_TOKEN", TOKEN)
    get_settings.cache_clear()
    reset_dev_token_cache()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    get_settings.cache_clear()
    reset_dev_token_cache()


def _operations():
    for path, item in sorted(app.openapi()["paths"].items()):
        for method in ("get", "post", "put", "patch", "delete"):
            if method in item:
                yield method.upper(), path


def _url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


def test_the_walk_covers_the_whole_api():
    assert len(list(_operations())) > 250


def test_every_operation_but_the_public_ones_rejects_a_missing_token(client):
    offenders = []
    for method, path in _operations():
        if (method, path) in PUBLIC:
            continue
        response = client.request(method, _url(path))
        if response.status_code != 401:
            offenders.append(f"{method} {path}: {response.status_code}")
    assert not offenders, "reachable without a token:\n" + "\n".join(offenders)


def test_every_operation_but_the_public_ones_rejects_a_wrong_token(client):
    offenders = []
    for method, path in _operations():
        if (method, path) in PUBLIC:
            continue
        response = client.request(method, _url(path), headers={"Authorization": "Bearer not-the-token"})
        if response.status_code != 401:
            offenders.append(f"{method} {path}: {response.status_code}")
    assert not offenders, "accepted a wrong token:\n" + "\n".join(offenders)


@pytest.mark.parametrize("method, path", sorted(PUBLIC))
def test_the_public_operations_answer_without_a_token(client, method, path):
    assert client.request(method, path).status_code == 200


@pytest.mark.parametrize("path", DOCS)
def test_the_api_docs_are_public(client, path):
    assert client.get(path).status_code == 200


def test_health_detail_is_not_public(client):
    assert client.get("/health/detailed").status_code == 401


def test_the_token_in_the_query_string_works_only_for_the_task_stream(client):
    # EventSource cannot set headers, so GET /api/tasks/<id>/stream accepts ?token= — nothing else may.
    assert client.get("/api/meta", params={"token": TOKEN}).status_code == 401
    assert client.get("/api/tasks/1/stream", params={"token": "wrong"}).status_code == 401
    assert client.post("/api/tasks/1/cancel", params={"token": TOKEN}).status_code == 401
    assert client.get("/api/tasks/1/stream", params={"token": TOKEN}).status_code != 401


@pytest.mark.parametrize(
    "path",
    ["/health/", "//health", "/HEALTH", "/health;x=1", "/health%2f..%2fapi%2fmeta", "/docs/../api/meta", "/api/auth/status/../meta"],
)
def test_lookalike_paths_do_not_open_a_private_route(client, path):
    """A path that merely resembles an open one — trailing slash, case, ``;`` parameters, encoded or dot segments — is not open."""
    assert client.get(path).status_code == 401


def test_a_valid_token_is_not_rejected_anywhere(client):
    """The other half of the walk: with the token, nothing answers 401 (some answer 404 / 422 / 503, which is not this test's business)."""
    rejected = []
    for method, path in _operations():
        if method != "GET" or "{" in path:
            continue
        response = client.get(_url(path), headers={"Authorization": f"Bearer {TOKEN}"})
        if response.status_code == 401:
            rejected.append(path)
    assert not rejected, rejected
