"""Pytest bootstrap — disable API auth so legacy TestClient tests keep working."""
from __future__ import annotations

import os

os.environ.setdefault("FORMUMIND_API_AUTH_ENABLED", "false")
os.environ.setdefault("FORMUMIND_ENVIRONMENT", "test")
