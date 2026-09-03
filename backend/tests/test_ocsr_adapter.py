"""OCSR 离线识别 adapter 单元测试（services/ocsr.py）。

MolScribe 不装在主 backend venv（独立 worker），这些测试验证 degradation
invariance — 缺库时所有调用返回中性值，管线行为不变。
"""
from __future__ import annotations

import pytest

from app.config import Settings, get_settings
from app.services import ocsr


@pytest.fixture(autouse=True)
def _fresh_settings(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ── degradation invariance（缺库 → 中性返回）────────────────────────────────


def test_molscribe_available_false_without_molscribe():
    assert ocsr.molscribe_available() is False


def test_predict_smiles_molscribe_absent():
    assert ocsr.predict_smiles_molscribe("/tmp/x.png") is None


def test_predict_smiles_local_absent():
    assert ocsr.predict_smiles_local("/tmp/x.png") is None


def test_prewarm_molscribe_noop_without_molscribe():
    assert ocsr.prewarm_molscribe() is False


def test_availability_structure(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ocsr, "_molscribe_worker_alive", lambda: False)
    a = ocsr.availability()
    assert set(a) == {"enabled", "molscribe_installed", "molscribe_queue", "molscribe_timeout_s"}
    assert a["molscribe_installed"] is False


def test_availability_reports_worker_alive(monkeypatch: pytest.MonkeyPatch):
    """molscribe_installed reflects the dedicated worker's liveness probe."""
    monkeypatch.setattr(ocsr, "_molscribe_worker_alive", lambda: True)
    assert ocsr.availability()["molscribe_installed"] is True
    monkeypatch.setattr(ocsr, "_molscribe_worker_alive", lambda: False)
    assert ocsr.availability()["molscribe_installed"] is False


# ── config 字段 ──────────────────────────────────────────────────────────────


def test_settings_have_ocsr_fields():
    s = Settings()
    assert s.ocsr_enabled is False
    assert s.molscribe_queue == "molscribe"
    assert s.molscribe_timeout_s == 180.0


# ── vision_extract._ocsr_direct 投递路由 ─────────────────────────────────────


def test_ocsr_direct_routes_to_molscribe_queue(monkeypatch):
    """_ocsr_direct 投递到 molscribe 队列（不真正发 Celery 任务）。"""
    from app.config import Settings as _Settings
    from app.services import vision_extract as ve

    captured: dict[str, str | None] = {}

    class _FakeResult:
        def get(self, timeout=None):
            return {"ok": True, "smiles": "CCO"}

    def _fake_send_task(name, args=None, queue=None):
        captured["name"] = name
        captured["queue"] = queue
        return _FakeResult()

    import app.worker.celery_app as ca

    monkeypatch.setattr(ca.celery_app, "send_task", _fake_send_task)

    s = _Settings(ocsr_enabled=True)
    out = ve._ocsr_direct(b"fake-png", s)
    assert captured["name"] == "formumind.molscribe_recognize"
    assert captured["queue"] == "molscribe"
    assert out is not None and out.molecules[0].smiles == "CCO"


def test_ocsr_direct_disabled_returns_none():
    from app.config import Settings as _Settings
    from app.services import vision_extract as ve

    s = _Settings(ocsr_enabled=False)
    assert ve._ocsr_direct(b"fake-png", s) is None
