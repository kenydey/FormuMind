"""Walk every documented operation with a minimal, schema-derived request — nothing may 500 or hang (round-4).

Found, in one pass, three handlers that turned caller mistakes into 500s (unknown example id, a recipe with no
ingredients, an unwritable pause flag) and a DOE-cycle step that waited on its own SQLite write lock: after
``POST /api/doe/cycle`` every request that wrote blocked for the 30 s busy timeout. None of it was reachable from
a unit test that injected its own session.

A response is acceptable when it is a 2xx/3xx/4xx, or a 503 (a feature that is switched off or a dependency that
is down). A 500 is a defect; a request that does not answer within the budget is a defect too.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

REQUEST_BUDGET_S = 30.0
# endpoints that stream, return files, or reach the public internet — not what this walk is for
SKIP = re.compile(
    r"/stream$|/events$|/download$|/export|/shutdown|/search|/research|/chat|/ingest/(url|task)|/connectors/mcp/call"
    r"|/skills/install|/mcp/import|/kb/golden-eval|/intent|/surechembl|/dependencies/install|/notebooklm|/molscribe"
)
METHODS = ("get", "post", "put", "patch", "delete")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    monkeypatch.setenv("FORMUMIND_DB_URL", f"sqlite:///{tmp_path}/walk.db")
    get_settings.cache_clear()
    from app.db.database import Base, default_engine

    Base.metadata.create_all(default_engine())
    yield TestClient(app, raise_server_exceptions=False)
    get_settings.cache_clear()


def _schemas() -> dict:
    return app.openapi()["components"]["schemas"]


def _deref(schema, schemas):
    while isinstance(schema, dict) and "$ref" in schema:
        schema = schemas[schema["$ref"].split("/")[-1]]
    return schema


def _build(schema, schemas, requirement, name: str = "", depth: int = 0):
    """The smallest value that satisfies *schema* (required fields only)."""
    schema = _deref(schema, schemas)
    if depth > 4 or not isinstance(schema, dict):
        return None
    props = schema.get("properties") or {}
    if "salt_spray_hours" in props and "domain" in props:  # a Requirement: a real one
        body = dict(requirement)
        for key in schema.get("required") or []:
            body.setdefault(key, _build(props.get(key, {}), schemas, requirement, key, depth + 1))
        return body
    if schema.get("default") is not None:
        return schema["default"]
    if "enum" in schema:
        return schema["enum"][0]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            options = [o for o in schema[key] if _deref(o, schemas).get("type") != "null"]
            return _build(options[0], schemas, requirement, name, depth + 1) if options else None
    if "allOf" in schema:
        merged: dict = {}
        for part in schema["allOf"]:
            built = _build(part, schemas, requirement, name, depth + 1)
            if isinstance(built, dict):
                merged.update(built)
        return merged
    kind = schema.get("type")
    if kind == "object" or props:
        return {k: _build(props.get(k, {}), schemas, requirement, k, depth + 1) for k in schema.get("required") or []}
    if kind == "array":
        return []
    if kind == "string":
        if schema.get("format") == "date-time":
            return "2026-01-01T00:00:00"
        return "1" if "id" in name else "x" * max(1, int(schema.get("minLength", 1)))
    if kind == "integer":
        return max(1, int(schema.get("minimum", 1)))
    if kind == "number":
        return float(schema.get("minimum", 1.0)) or 1.0
    if kind == "boolean":
        return False
    return None


def _operations():
    spec = app.openapi()
    for path, item in sorted(spec["paths"].items()):
        for method in METHODS:
            if method in item and not SKIP.search(path):
                yield method, path, item[method]


def _request_for(method: str, path: str, op: dict, requirement: dict) -> tuple[str, dict]:
    schemas = _schemas()
    url = re.sub(
        r"\{([^}]+)\}",
        lambda m: "1" if "id" in m.group(1) or _is_int(op, m.group(1), schemas) else "x",
        path,
    )
    kwargs: dict = {"params": {}}
    for prm in op.get("parameters", []):
        if prm["in"] == "query" and prm.get("required"):
            kwargs["params"][prm["name"]] = _build(prm.get("schema", {}), schemas, requirement, prm["name"])
    body = (op.get("requestBody") or {}).get("content", {})
    if "application/json" in body:
        kwargs["json"] = _build(body["application/json"].get("schema", {}), schemas, requirement, "body")
    elif "multipart/form-data" in body:
        schema = _deref(body["multipart/form-data"].get("schema", {}), schemas)
        files, data = {}, {}
        for key, prop in (schema.get("properties") or {}).items():
            prop = _deref(prop, schemas)
            if prop.get("format") == "binary" or "file" in key or "image" in key:
                files[key] = ("probe.txt", b"hello 1000 h", "text/plain")
            elif key in (schema.get("required") or []):
                data[key] = _build(prop, schemas, requirement, key)
        if files:
            kwargs["files"] = files
        if data:
            kwargs["data"] = data
    return url, kwargs


def _is_int(op: dict, name: str, schemas: dict) -> bool:
    for prm in op.get("parameters", []):
        if prm["in"] == "path" and prm["name"] == name:
            return _deref(prm.get("schema", {}), schemas).get("type") == "integer"
    return False


def test_the_walk_reaches_a_meaningful_part_of_the_api():
    assert len(list(_operations())) > 150


def test_no_documented_operation_answers_500_or_hangs(client):
    from app.domain.project_workspace import default_requirement
    from app.middleware.rate_limit import reset_rate_limits

    requirement = json.loads(default_requirement().model_dump_json())
    offenders: list[str] = []
    for method, path, op in _operations():
        if method == "delete" and "json" in _request_for(method, path, op, requirement)[1]:
            # TestClient.delete() takes no body; a DELETE with one is not what the UI sends anyway
            continue
        url, kwargs = _request_for(method, path, op, requirement)
        reset_rate_limits()
        pool = cf.ThreadPoolExecutor(max_workers=1)
        started = time.monotonic()
        future = pool.submit(lambda: getattr(client, method)(url, **kwargs))
        try:
            response = future.result(timeout=REQUEST_BUDGET_S)
        except cf.TimeoutError:
            offenders.append(f"{method.upper()} {path}: no answer within {REQUEST_BUDGET_S:.0f}s")
            pool.shutdown(wait=False)
            break  # everything after a hang is noise (it holds locks); report the first one
        except Exception as exc:  # noqa: BLE001
            offenders.append(f"{method.upper()} {path}: {type(exc).__name__}: {exc}")
        else:
            if response.status_code >= 500 and response.status_code != 503:
                offenders.append(f"{method.upper()} {path}: HTTP {response.status_code} {response.text[:120]!r}")
        finally:
            pool.shutdown(wait=False)
        assert time.monotonic() - started < REQUEST_BUDGET_S + 5
    assert not offenders, "\n".join(offenders)
