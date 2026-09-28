"""Pytest bootstrap — disable API auth so legacy TestClient tests keep working."""
from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("FORMUMIND_API_AUTH_ENABLED", "false")
os.environ.setdefault("FORMUMIND_ENVIRONMENT", "test")
# Datalab 已非 TESTING 公开模式：legacy TestClient 测试不得直连真平台。
# Datalab 专项测试显式构造 store/override settings，不受此默认值影响。
# Product defaults are datalab + DATALAB_REQUIRED=true; CI/unit tests isolate
# on explicit sqlite (not a product "no ELN" mode).
os.environ.setdefault("FORMUMIND_CAMPAIGN_BACKEND", "sqlite")
os.environ.setdefault("FORMUMIND_EXPERIMENT_BACKEND", "sqlite")
os.environ.setdefault("FORMUMIND_DATALAB_REQUIRED", "false")
# Test speed-up: skip heavy lifespan bootstrap (ColBERT seed corpus, settings
# reload, PubChem enrichment). Default/production behaviour is unchanged.
os.environ.setdefault("FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP", "1")
# Async KB ingest spawns background fetch threads after search tasks; keep the
# suite offline/deterministic — tests that exercise it enable it explicitly
# with stubbed fetchers.
os.environ.setdefault("FORMUMIND_KB_INGEST_AUTO", "false")
# Production defaults celery_eager=False (real worker + Redis). Offline CI has
# no broker — run tasks in-process so 202/SSE suites stay green. Tests that
# assert broker-down behaviour monkeypatch celery_eager=False explicitly.
os.environ.setdefault("FORMUMIND_CELERY_EAGER", "true")
# Settings persistence (LLM / secrets / env flags) writes a .env file; point it
# at a session-scoped temp file so tests never touch the repo-root .env.
os.environ.setdefault(
    "FORMUMIND_ENV_FILE",
    os.path.join(tempfile.mkdtemp(prefix="formumind-test-env-"), ".env"),
)


# Snapshot os.environ before any test module is imported. Some third-party
# libraries (magika via markitdown, litellm) call
# ``dotenv.load_dotenv(dotenv.find_dotenv())`` at import time, which dumps the
# repo-root .env into os.environ permanently. Test isolation requires the
# repo-root .env to stay out of os.environ (FORMUMIND_ENV_FILE points at a
# temp file for exactly this reason), so import-time additions are reverted
# in pytest_collection_finish below.
_OS_ENVIRON_BASELINE = dict(os.environ)


def _revert_environ_pollution() -> None:
    for key in list(os.environ):
        if key.startswith("FORMUMIND_") and key not in _OS_ENVIRON_BASELINE:
            del os.environ[key]
    try:
        from app.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


def pytest_collection_finish(session):
    """Undo import-time os.environ pollution (see _OS_ENVIRON_BASELINE)."""
    _revert_environ_pollution()


def pytest_configure():
    """Keep Settings cache + Celery eager flag aligned with the test env.

    ``celery_app`` snapshots ``task_always_eager`` at import time. If Settings
    were cached earlier (or a test toggled the env), probe/dispatch can think
    eager is on while ``.delay()`` still talks to Redis and hangs until the
    10s dispatch timeout → false 503s in CI.
    """
    try:
        from app.config import get_settings

        get_settings.cache_clear()
        eager = bool(get_settings().celery_eager)
        from app.worker.celery_app import celery_app

        celery_app.conf.task_always_eager = eager
        celery_app.conf.task_eager_propagates = True
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_rate_limits_before_test():
    """Clear in-memory rate-limit buckets so tests start from a clean state."""
    try:
        from app.middleware.rate_limit import reset_rate_limits

        reset_rate_limits()
    except Exception:
        pass
    yield
    # Re-sync after tests that monkeypatch celery_eager / clear Settings cache.
    try:
        from app.config import get_settings
        from app.worker.celery_app import celery_app

        celery_app.conf.task_always_eager = bool(get_settings().celery_eager)
    except Exception:
        pass
    # Drop the cached Settings so env mutations via monkeypatch.setenv in one
    # test file cannot leak into the next (lru_cache otherwise holds a stale
    # object after monkeypatch reverts the env vars). Also revert os.environ
    # pollution from libraries that load_dotenv() at import time inside a test
    # (lazy imports); import-time pollution is handled in
    # pytest_collection_finish.
    _revert_environ_pollution()

