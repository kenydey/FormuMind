"""Settings extra-field policy by environment."""
from __future__ import annotations

import pytest

from app.config import _settings_extra_policy, get_settings


def test_extra_policy_development(monkeypatch):
    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "development")
    assert _settings_extra_policy() == "forbid"


def test_extra_policy_production(monkeypatch):
    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "production")
    assert _settings_extra_policy() == "ignore"


def test_unknown_env_var_fails_in_development(monkeypatch):
    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "development")
    monkeypatch.setenv("FORMUMIND_NOT_A_REAL_SETTING", "typo-value")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="Unknown FORMUMIND"):
        get_settings()
    monkeypatch.delenv("FORMUMIND_NOT_A_REAL_SETTING", raising=False)
    get_settings.cache_clear()


def test_unknown_env_var_ignored_in_production(monkeypatch):
    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "production")
    monkeypatch.setenv("FORMUMIND_NOT_A_REAL_SETTING", "typo-value")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.app_name == "FormuMind"
    monkeypatch.delenv("FORMUMIND_NOT_A_REAL_SETTING", raising=False)
    get_settings.cache_clear()


def test_bo_quality_env_key_accepted_in_development(monkeypatch):
    """A-2: FORMUMIND_BO_QUALITY is documented in baybe_engine (_recommender_for
    tiers fast/auto/thorough) and must not be rejected by the dev/test env audit."""
    import os

    from app.config import _audit_formumind_env

    monkeypatch.setenv("FORMUMIND_ENVIRONMENT", "development")
    for tier in ("fast", "auto", "thorough"):
        monkeypatch.setenv("FORMUMIND_BO_QUALITY", tier)
        _audit_formumind_env()  # must not raise
    # Unknown keys are still rejected.
    monkeypatch.setenv("FORMUMIND_BO_QUALITTY", "thorough")
    try:
        with pytest.raises(ValueError, match="Unknown FORMUMIND"):
            _audit_formumind_env()
    finally:
        monkeypatch.delenv("FORMUMIND_BO_QUALITTY", raising=False)
