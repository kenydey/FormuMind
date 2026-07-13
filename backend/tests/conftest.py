"""Pytest bootstrap — disable API auth so legacy TestClient tests keep working."""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("FORMUMIND_API_AUTH_ENABLED", "false")
os.environ.setdefault("FORMUMIND_ENVIRONMENT", "test")
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
