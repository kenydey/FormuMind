"""Guard: a Settings field nobody reads is a dead toggle.

Round-3 audit found six of them (``auto_retrain``, ``kg_link_on_ingest``,
``chat_composer_plus_enabled``, ``chat_rerank_enabled``, ``pdf_download``,
``pdf_download_max``): the Settings UI offered a switch, users flipped it, and
nothing changed. Each is now wired or removed; this test keeps it that way.

A field counts as *read* when backend code (outside ``config.py`` — the
declaration — and ``env_flags.py`` — the UI registry) touches it as an
attribute, a keyword, a bare name, or an exact string key (``getattr(s,
"name")``), or when the frontend refers to it (UI toggles are read through
``/api/settings/env-flags``). Comments and docstrings do not count.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from app.config import Settings

BACKEND = Path(__file__).resolve().parents[1]
FRONTEND_SRC = BACKEND.parent / "frontend" / "src"

# Fields that are deliberately not "read" by name. Keep this empty if you can;
# every entry needs a reason.
ALLOWED_UNREAD: dict[str, str] = {}


def _backend_reads() -> set[str]:
    reads: set[str] = set()
    # scripts/ counts: e.g. rigor_gate.py reads evals_rigor_thresholds for CI.
    paths = [*(BACKEND / "app").rglob("*.py"), *(BACKEND / "scripts").rglob("*.py")]
    for path in paths:
        if path.name in {"config.py", "env_flags.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                reads.add(node.attr)
            elif isinstance(node, ast.Name):
                reads.add(node.id)
            elif isinstance(node, ast.keyword) and node.arg:
                reads.add(node.arg)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if re.fullmatch(r"[a-z][a-z0-9_]*", node.value):
                    reads.add(node.value)
    return reads


def _frontend_blob() -> str:
    parts = []
    for path in FRONTEND_SRC.rglob("*"):
        if path.suffix in {".ts", ".tsx"} and ".test." not in path.name:
            parts.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def test_every_settings_field_has_a_reader():
    if not FRONTEND_SRC.is_dir():
        pytest.skip("frontend sources not available (backend-only checkout)")
    backend = _backend_reads()
    frontend = _frontend_blob()
    unread = sorted(
        name
        for name in Settings.model_fields
        if name not in backend
        and not re.search(rf"\b{re.escape(name)}\b", frontend)
        and name not in ALLOWED_UNREAD
    )
    assert not unread, (
        "Settings fields that nothing reads (wire them or delete them): "
        + ", ".join(unread)
    )


def test_allowlist_has_no_stale_entries():
    assert all(name in Settings.model_fields for name in ALLOWED_UNREAD)


def test_retired_env_keys_are_tolerated_but_not_settings_fields(monkeypatch):
    """Old .env files may still carry removed keys; they must not crash startup."""
    from app import config

    for key in config._RETIRED_ENV_KEYS:
        assert key[len("FORMUMIND_"):].lower() not in Settings.model_fields
        monkeypatch.setenv(key, "true")
    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "development")
    config.get_settings.cache_clear()
    try:
        config.get_settings()  # would raise "Unknown FORMUMIND_* environment variables"
    finally:
        for key in config._RETIRED_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        config.get_settings.cache_clear()


def test_a_genuinely_unknown_env_key_still_fails_fast_in_dev(monkeypatch):
    from app import config

    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "development")
    monkeypatch.setenv("FORMUMIND_TOTALLY_MADE_UP_KEY", "1")
    config.get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="TOTALLY_MADE_UP_KEY"):
            config.get_settings()
    finally:
        monkeypatch.delenv("FORMUMIND_TOTALLY_MADE_UP_KEY", raising=False)
        config.get_settings.cache_clear()
