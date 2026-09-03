"""Fast-path tests for FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP (Task 0.3).

Verifies that the lifespan bootstrap fast-path keeps /health fast under the
test fixture flag and that the flag actually gates the heavy bootstrap steps.
"""
from __future__ import annotations

import importlib
import os
import time

from fastapi.testclient import TestClient


def test_health_fast_under_5s():
    """/health via TestClient (full lifespan) must complete well under 5s
    when FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP is set by tests/conftest.py."""
    from app.main import app

    start = time.perf_counter()
    with TestClient(app) as client:
        resp = client.get("/health")
    elapsed = time.perf_counter() - start

    assert resp.status_code == 200
    assert elapsed < 5.0, f"/health lifespan round-trip took {elapsed:.2f}s (>= 5s)"


def test_bootstrap_skipped_when_flag_set(monkeypatch):
    """With the flag set, the heavy ColBERT seed bootstrap must not run."""
    import app.main as main_module

    calls = {"count": 0}

    import app.services.colbert_store as colbert_store

    def _fake_bootstrap():
        calls["count"] += 1

    monkeypatch.setattr(colbert_store, "bootstrap_seed_corpus", _fake_bootstrap)
    monkeypatch.setenv("FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP", "1")

    import asyncio

    async def _run_lifespan():
        async with main_module.lifespan(main_module.app):
            pass

    asyncio.run(_run_lifespan())
    assert calls["count"] == 0, "bootstrap_seed_corpus ran despite skip flag"


def test_flag_truthiness_helper(monkeypatch):
    """The flag parser accepts 1/true/yes (case-insensitive) and rejects others."""
    from app.main import _skip_lifespan_bootstrap

    for truthy in ("1", "true", "TRUE", "Yes", "yes"):
        monkeypatch.setenv("FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP", truthy)
        assert _skip_lifespan_bootstrap() is True, truthy
    for falsy in ("0", "false", "no", "", "nope"):
        monkeypatch.setenv("FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP", falsy)
        assert _skip_lifespan_bootstrap() is False, falsy
    monkeypatch.delenv("FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP", raising=False)
    assert _skip_lifespan_bootstrap() is False
