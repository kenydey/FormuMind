"""Pytest bootstrap — disable API auth so legacy TestClient tests keep working."""
from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("FORMUMIND_API_AUTH_ENABLED", "false")
os.environ.setdefault("FORMUMIND_ENVIRONMENT", "test")
# Test speed-up: skip heavy lifespan bootstrap (ColBERT seed corpus, settings
# reload, PubChem enrichment). Default/production behaviour is unchanged.
os.environ.setdefault("FORMUMIND_SKIP_LIFESPAN_BOOTSTRAP", "1")
# Async KB ingest spawns background fetch threads after search tasks; keep the
# suite offline/deterministic — tests that exercise it enable it explicitly
# with stubbed fetchers.
os.environ.setdefault("FORMUMIND_KB_INGEST_AUTO", "false")
# Settings persistence (LLM / secrets / env flags) writes a .env file; point it
# at a session-scoped temp file so tests never touch the repo-root .env.
os.environ.setdefault(
    "FORMUMIND_ENV_FILE",
    os.path.join(tempfile.mkdtemp(prefix="formumind-test-env-"), ".env"),
)


@pytest.fixture(autouse=True)
def _reset_rate_limits_before_test():
    """Clear in-memory rate-limit buckets so tests start from a clean state."""
    try:
        from app.middleware.rate_limit import reset_rate_limits

        reset_rate_limits()
    except Exception:
        pass
    yield

