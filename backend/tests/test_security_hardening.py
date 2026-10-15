"""Regression tests for the /debug findings: auth, ``.env`` writes, downloads, error classes.

Each of these was reproduced against the running application before it was fixed.
"""
from __future__ import annotations

import json
import os
import stat
import threading

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

from app.api._downloads import ascii_header_value, attachment_headers
from app.config import get_settings
from app.main import app
from app.middleware.api_auth import get_current_owner, reset_dev_token_cache
from app.services import secrets_store as ss
from app.services.errors import classify_exception

TOKEN = "platform-token-123"
ORIGIN = "http://localhost:5173"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_API_TOKEN", TOKEN)
    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "test")
    monkeypatch.delenv("FORMUMIND_MULTI_USER", raising=False)
    monkeypatch.delenv("FORMUMIND_API_TOKENS_JSON", raising=False)
    get_settings.cache_clear()
    reset_dev_token_cache()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    get_settings.cache_clear()
    reset_dev_token_cache()


def _owner_request(token: str):
    from starlette.requests import Request

    return Request({"type": "http", "method": "GET", "path": "/api/x", "headers": [(b"authorization", f"Bearer {token}".encode())], "query_string": b""})


# ── auth ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_non_ascii_token_is_a_401_not_a_500(client):
    r = client.get("/api/meta", headers={"Authorization": "Bearer é".encode("latin-1")})
    assert r.status_code == 401


def test_a_valid_token_still_passes(client):
    assert client.get("/api/meta", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_an_owner_name_is_not_a_credential(client, monkeypatch):
    monkeypatch.setenv("FORMUMIND_MULTI_USER", "true")
    monkeypatch.setenv("FORMUMIND_API_TOKENS_JSON", json.dumps({"alice": "tok-alice-1"}))
    assert client.get("/api/meta", headers={"Authorization": "Bearer alice"}).status_code == 401
    assert client.get("/api/meta", headers={"Authorization": "Bearer tok-alice-1"}).status_code == 200


def test_the_owner_comes_from_the_mapping_never_from_the_token_text(monkeypatch):
    monkeypatch.setenv("FORMUMIND_MULTI_USER", "true")
    monkeypatch.setenv("FORMUMIND_API_TOKENS_JSON", json.dumps({"alice": "tok-alice-1", "bob": "tok-bob-2"}))
    assert get_current_owner(_owner_request("tok-bob-2")) == "bob"
    assert get_current_owner(_owner_request("mallory:tok-bob-2")) == "default"  # no "owner:token" shortcut
    monkeypatch.delenv("FORMUMIND_API_TOKENS_JSON")
    owner = get_current_owner(_owner_request("sk-super-secret-token-value"))
    assert "sk-super" not in owner and owner.startswith("u-")  # a hash, not a slice of the credential


def test_error_answers_carry_cors_headers(client):
    r = client.get("/api/meta", headers={"Origin": ORIGIN})
    assert r.status_code == 401
    assert r.headers.get("access-control-allow-origin") == ORIGIN


# ── .env writes ──────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("FORMUMIND_ENVIRONMENT=test\n", encoding="utf-8")
    monkeypatch.setenv("FORMUMIND_ENV_FILE", str(path))
    return path


def test_a_line_break_in_a_secret_is_rejected_and_nothing_is_written(client, env_file):
    r = client.post(
        "/api/settings/secrets",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"updates": {"tavily_api_key": "x\nFORMUMIND_API_AUTH_ENABLED=false"}},
    )
    assert r.status_code == 422
    assert env_file.read_text(encoding="utf-8") == "FORMUMIND_ENVIRONMENT=test\n"


def test_a_line_break_in_an_llm_setting_is_rejected(client, env_file):
    r = client.post(
        "/api/settings",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"provider": "deepseek", "model": "m\nFORMUMIND_DEPS_INSTALL_ENABLED=true"},
    )
    assert r.status_code == 422
    assert "DEPS_INSTALL" not in env_file.read_text(encoding="utf-8")


def test_a_rejected_secret_leaves_no_half_applied_batch(env_file):
    with pytest.raises(ValueError):
        ss.update_secrets({"tavily_api_key": "fine-value", "serpapi_api_key": "bad\nvalue"})
    assert "tavily" not in env_file.read_text(encoding="utf-8").lower()


@pytest.mark.parametrize(
    "url",
    ["http://169.254.169.254/latest", "http://metadata.google.internal/", "file:///etc/passwd", "http://u:p@datalab:5001", "http://"],
)
def test_datalab_url_rejects_metadata_and_non_http_targets(url):
    with pytest.raises(ValueError):
        ss.validate_datalab_url(url)


@pytest.mark.parametrize("url", ["http://localhost:5001", "http://datalab:5001", "http://host.docker.internal:5001", "https://eln.example.org"])
def test_datalab_url_allows_the_normal_deployments(url):
    assert ss.validate_datalab_url(url) == url


def test_the_env_file_is_private_and_concurrent_saves_keep_every_key(env_file):
    keys = [f"FORMUMIND_TEST_KEY_{i}" for i in range(12)]
    threads = [threading.Thread(target=ss.write_env_updates, args=({k: f"v{i}"},)) for i, k in enumerate(keys)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    saved = ss.read_env_file(env_file)
    assert all(saved.get(k) == f"v{i}" for i, k in enumerate(keys)), saved
    if os.name != "nt":
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_a_secret_is_masked_to_its_last_four_characters_at_most():
    assert ss._mask("abcdefghijkl") == "••••"  # 12 chars: nothing
    assert ss._mask("a" * 20 + "WXYZ") == "••••WXYZ"
    assert ss._mask(None) == ""


# ── downloads ────────────────────────────────────────────────────────────────────────────────────────────────────────

def _download_app(name: str) -> TestClient:
    sub = FastAPI()

    @sub.get("/f")
    def f():
        return Response(b"x", headers=attachment_headers(name))

    return TestClient(sub, raise_server_exceptions=False)


def test_a_chinese_file_name_is_a_download_not_a_500():
    r = _download_app("实验报告.pdf").get("/f")
    assert r.status_code == 200
    assert "filename*=UTF-8''%E5%AE%9E%E9%AA%8C%E6%8A%A5%E5%91%8A.pdf" in r.headers["content-disposition"]


def test_a_quote_in_a_file_name_cannot_add_header_parameters():
    value = attachment_headers('a"; x=1.pdf')["Content-Disposition"]
    assert value.count('"') == 2  # only the delimiters of the ASCII fallback
    assert ascii_header_value("/tmp/报告\n.md").isascii()


# ── error classes ────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code, kind", [(503, "transient"), (502, "transient"), (429, "transient"), (404, "permanent"), (400, "permanent")])
def test_http_status_errors_are_classified_by_status(code, kind):
    request = httpx.Request("GET", "http://x")
    exc = httpx.HTTPStatusError("e", request=request, response=httpx.Response(code, request=request))
    assert classify_exception(exc) == kind


# ── operations: rate-limit key, access log, /health ──────────────────────────────────────────────────────────────────

def _request(xff: str | None, peer: str = "172.18.0.5"):
    from starlette.requests import Request

    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "query_string": b"", "client": (peer, 1234)})


def test_the_rate_limit_key_is_the_peer_unless_proxies_are_trusted(monkeypatch):
    from app.middleware.rate_limit import _client_ip

    monkeypatch.setenv("FORMUMIND_TRUSTED_PROXY_HOPS", "0")
    get_settings.cache_clear()
    assert _client_ip(_request("203.0.113.9")) == "172.18.0.5"  # header ignored: a client could rotate it at will
    monkeypatch.setenv("FORMUMIND_TRUSTED_PROXY_HOPS", "1")
    get_settings.cache_clear()
    # the client wrote "6.6.6.6"; nginx appended the address it really saw
    assert _client_ip(_request("6.6.6.6, 203.0.113.9")) == "203.0.113.9"
    assert _client_ip(_request(None)) == "172.18.0.5"  # no header (direct call): fall back to the peer
    get_settings.cache_clear()


def test_the_access_log_does_not_carry_the_stream_token():
    import logging

    from app.main import _RedactQueryToken

    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("1.2.3.4:5", "GET", "/api/tasks/abc/stream?token=sk-secret&x=1", "1.1", 200), None)
    assert _RedactQueryToken().filter(record) is True
    assert "sk-secret" not in record.getMessage() and "token=***&x=1" in record.getMessage()


def test_health_is_probed_once_per_window_and_never_blocks_on_a_probe_in_flight(monkeypatch):
    import app.main as main

    main.reset_health_cache()
    calls = []

    def probe():
        calls.append(1)
        return {"status": "ok"}

    monkeypatch.setattr(main, "_probe_health", probe)
    assert main.health() == {"status": "ok"} and main.health() == {"status": "ok"}
    assert len(calls) == 1
    # window over, but another request is already probing: answer with the previous result at once
    main._health_cache["at"] = 0.0
    assert main._health_lock.acquire(blocking=False)
    try:
        assert main.health() == {"status": "ok"}
        assert len(calls) == 1
    finally:
        main._health_lock.release()
    main.reset_health_cache()


# ── task ids never become part of a path ─────────────────────────────────────────────────────────────────────────────

def test_a_task_id_that_is_not_a_plain_token_is_not_written_or_read(tmp_path, monkeypatch):
    from app.domain.schemas import TaskState, TaskStatus
    from app.worker import tasks as T

    task_dir = tmp_path / "tasks"
    monkeypatch.setattr(T, "_task_persist_dir", lambda: task_dir)
    status = TaskStatus(task_id="x", kind="k", state=TaskState.completed)
    for bad in ("../ESCAPED", "a/b", "a\\b", "", "x" * 65, "ok id"):
        T._persist_task(bad, status)
        assert T.load_persisted_task(bad) is None
    assert not (tmp_path / "ESCAPED.json").exists()
    assert not task_dir.exists() or not list(task_dir.iterdir())
    T._persist_task("fileingest-0123456789abcdef", status)  # the real shapes still work
    T._persist_task("3f2b8c1e-9d4a-4b6e-8a1f-0c2d3e4f5a6b", status)
    assert T.load_persisted_task("fileingest-0123456789abcdef").task_id == "x"
