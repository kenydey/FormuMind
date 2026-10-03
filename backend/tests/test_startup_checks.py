"""Tests for backend/app/startup_checks.py — production fail-fast checks."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.startup_checks import run_startup_checks


def _settings(**overrides):
    base = {
        "db_url": "sqlite:///./data/formumind.db",
        "datalab_required": False,
        "datalab_api_url": "http://localhost:5001",
        "environment": "development",
        "api_auth_enabled": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_happy_path_no_errors_no_warnings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    warnings = run_startup_checks(_settings())
    assert warnings == []


def test_unsupported_db_scheme_fails_fast():
    with pytest.raises(RuntimeError, match="FORMUMIND_DB_URL.*unsupported scheme"):
        run_startup_checks(_settings(db_url="mysql://user@host/db"))


def test_missing_sqlite_data_dir_fails_fast(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # ./data does not exist here
    with pytest.raises(RuntimeError, match="data directory.*does not exist"):
        run_startup_checks(_settings(db_url="sqlite:///./data/formumind.db"))


def test_unwritable_sqlite_data_dir_fails_fast(tmp_path, monkeypatch):
    # Running as root ignores permission bits, so simulate via os.access.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    with patch("app.startup_checks.os.access", return_value=False):
        with pytest.raises(RuntimeError, match="not writable"):
            run_startup_checks(_settings(db_url="sqlite:///./data/formumind.db"))


def test_datalab_required_without_url_fails_fast(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    with pytest.raises(RuntimeError, match="FORMUMIND_DATALAB_API_URL"):
        run_startup_checks(
            _settings(datalab_required=True, datalab_api_url="  ")
        )


def test_datalab_required_with_url_passes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    warnings = run_startup_checks(
        _settings(datalab_required=True, datalab_api_url="http://datalab:5001")
    )
    assert warnings == []


def test_postgres_url_skips_filesystem_check():
    warnings = run_startup_checks(
        _settings(db_url="postgresql://user:pw@db:5432/formumind")
    )
    assert warnings == []


def test_auth_disabled_in_production_warns_not_fails(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    warnings = run_startup_checks(
        _settings(environment="production", api_auth_enabled=False)
    )
    assert len(warnings) == 1
    assert "unauthenticated" in warnings[0]


def test_multiple_errors_all_reported(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError) as exc_info:
        run_startup_checks(
            _settings(
                db_url="oracle://host/db",
                datalab_required=True,
                datalab_api_url="",
            )
        )
    msg = str(exc_info.value)
    assert "unsupported scheme" in msg
    assert "FORMUMIND_DATALAB_API_URL" in msg


# ── Neo4j password ───────────────────────────────────────────────────────────


def test_production_with_the_shipped_neo4j_password_warns(monkeypatch):
    monkeypatch.delenv("FORMUMIND_NEO4J_PASSWORD", raising=False)
    with patch("app.services.neo4j_kg._env_or_file", side_effect=lambda name, default="": default):
        warnings = run_startup_checks(
            _settings(environment="production", api_auth_enabled=True, neo4j_enabled=True)
        )
    assert any("FORMUMIND_NEO4J_PASSWORD" in w for w in warnings)


def test_a_configured_neo4j_password_is_quiet():
    with patch("app.services.neo4j_kg._env_or_file", return_value="a-long-random-secret"):
        warnings = run_startup_checks(
            _settings(environment="production", api_auth_enabled=True, neo4j_enabled=True)
        )
    assert not any("NEO4J" in w for w in warnings)


def test_neo4j_password_is_only_a_production_concern_when_the_graph_is_on():
    with patch("app.services.neo4j_kg._env_or_file", side_effect=lambda name, default="": default):
        dev = run_startup_checks(_settings(environment="development", neo4j_enabled=True))
        off = run_startup_checks(
            _settings(environment="production", api_auth_enabled=True, neo4j_enabled=False)
        )
    assert not any("NEO4J" in w for w in dev + off)
