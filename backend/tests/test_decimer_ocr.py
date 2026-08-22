"""DECIMER offline OCSR adapter tests (services/decimer_ocr.py).

DECIMER (TensorFlow) is not installed in CI / the main backend venv, so these
tests exercise two contracts:
1. degradation invariance — every adapter call returns a neutral value when
   DECIMER is absent or the switch is off;
2. fake-DECIMER injection — via a fake ``DECIMER`` module in ``sys.modules``.
"""
from __future__ import annotations

import sys
import types

import pytest

from app.config import Settings, get_settings
from app.services import decimer_ocr
from app.services.env_flags import list_env_flags


@pytest.fixture(autouse=True)
def _fresh_settings(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ── degradation invariance (DECIMER absent in CI) ────────────────────────────


def test_decimer_available_false_without_decimer():
    assert decimer_ocr.decimer_available() is False


def test_resolve_decimer_mode_default_auto_is_cpu():
    # no tensorflow in the main venv → auto falls back to cpu
    assert decimer_ocr.resolve_decimer_mode() == "cpu"


def test_resolve_decimer_mode_explicit(monkeypatch):
    monkeypatch.setenv("FORMUMIND_DECIMER_MODE", "gpu")
    get_settings.cache_clear()
    assert decimer_ocr.resolve_decimer_mode() == "gpu"

    monkeypatch.setenv("FORMUMIND_DECIMER_MODE", "cpu")
    get_settings.cache_clear()
    assert decimer_ocr.resolve_decimer_mode() == "cpu"


def test_predict_smiles_local_absent():
    assert decimer_ocr.predict_smiles_local("/tmp/x.png") is None


def test_availability_structure():
    a = decimer_ocr.availability()
    assert set(a) >= {
        "enabled",
        "mode",
        "installed_in_process",
        "queue",
        "segmentation",
        "timeout_s",
    }
    assert a["installed_in_process"] is False
    assert a["enabled"] is False  # default off


# ── fake-DECIMER injection ───────────────────────────────────────────────────


def _install_fake_decimer(monkeypatch, canned_smiles="CN1C=NC2=C1C(=O)N(C)C(=O)N2C"):
    mod = types.ModuleType("DECIMER")

    def predict_SMILES(image_path):  # noqa: N802
        return canned_smiles

    mod.predict_SMILES = predict_SMILES
    monkeypatch.setitem(sys.modules, "DECIMER", mod)


def test_predict_smiles_local_with_fake(monkeypatch):
    _install_fake_decimer(monkeypatch)
    assert decimer_ocr.decimer_available() is True
    assert decimer_ocr.predict_smiles_local("/tmp/x.png") == "CN1C=NC2=C1C(=O)N(C)C(=O)N2C"


def test_predict_smiles_local_fake_raises(monkeypatch):
    mod = types.ModuleType("DECIMER")

    def predict_SMILES(image_path):  # noqa: N802
        raise RuntimeError("boom")

    mod.predict_SMILES = predict_SMILES
    monkeypatch.setitem(sys.modules, "DECIMER", mod)
    assert decimer_ocr.predict_smiles_local("/tmp/x.png") is None


# ── config + env-flags integration ───────────────────────────────────────────


def test_settings_have_decimer_fields():
    s = Settings()
    assert hasattr(s, "decimer_enabled")
    assert s.decimer_enabled is False
    assert s.decimer_mode == "auto"
    assert s.decimer_threads == 1
    assert s.decimer_queue == "decimer"


def test_env_flag_registered():
    attrs = {f["attr"] for f in list_env_flags()}
    assert "decimer_enabled" in attrs


# ── prewarm (worker boot, not per task) ──────────────────────────────────────


def test_prewarm_is_a_noop_without_decimer():
    assert decimer_ocr.prewarm_decimer() is False


def test_prewarm_imports_decimer_when_present(monkeypatch):
    _install_fake_decimer(monkeypatch)
    assert decimer_ocr.prewarm_decimer() is True


def test_prewarm_swallows_import_errors(monkeypatch):
    """decimer_available() 为真但 predict_SMILES 缺失时，预热吞掉错误返回 False。"""
    monkeypatch.setitem(sys.modules, "DECIMER", types.ModuleType("DECIMER"))
    assert decimer_ocr.prewarm_decimer() is False
