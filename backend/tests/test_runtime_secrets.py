"""Tests for thread-safe runtime secrets overlay."""
from __future__ import annotations

import threading

import pytest

from app.config import Settings, get_settings
from app.services.runtime_secrets import get_runtime_secrets, reset_runtime_secrets
from app.services.secrets_store import SECRET_REGISTRY, reload_settings, update_secrets


@pytest.fixture(autouse=True)
def _clean_runtime_secrets(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.setenv("FORMUMIND_ENV_FILE", str(env_file))
    get_settings.cache_clear()
    reset_runtime_secrets()
    reload_settings()
    yield
    get_settings.cache_clear()
    reset_runtime_secrets()


def test_runtime_secrets_concurrent_updates():
    errors: list[str] = []
    barrier = threading.Barrier(10)

    def worker(i: int) -> None:
        try:
            barrier.wait(timeout=5)
            update_secrets({"serpapi_api_key": f"key-{i}"})
            val = get_runtime_secrets().effective(get_settings(), "serpapi_api_key")
            if not val or not val.startswith("key-"):
                errors.append(f"unexpected value: {val!r}")
        except Exception as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors
    final = get_runtime_secrets().effective(get_settings(), "serpapi_api_key")
    assert final and final.startswith("key-")


def test_settings_singleton_unchanged_after_secret_update():
    before = get_settings()
    before_id = id(before)
    update_secrets({"tavily_api_key": "tvly-abc"})
    after = get_settings()
    assert id(after) == before_id
    assert get_runtime_secrets().effective(after, "tavily_api_key") == "tvly-abc"


def test_get_active_api_key_reads_runtime_overlay():
    s = get_settings()
    rs = get_runtime_secrets()
    rs.set("llm_provider", "deepseek")
    rs.set("deepseek_api_key", "sk-runtime")
    assert s.get_active_api_key() == "sk-runtime"


def test_bootstrap_loads_secret_registry_attrs():
    rs = get_runtime_secrets()
    attrs = {item[0] for item in SECRET_REGISTRY}
    snap = rs.snapshot()
    assert attrs.issubset(snap.keys())
