"""OCSR 多后端 adapter 单元测试（services/ocsr.py）。

MolScribe / DECIMER 都不装在主 backend venv（各自独立 worker），这些测试验证
两个契约：
1. degradation invariance — 缺库时所有调用返回中性值，管线行为不变；
2. 后端分发 — auto 无 GPU → molscribe，显式 molscribe/decimer 直接返回。
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


# ── 后端分发 ────────────────────────────────────────────────────────────────


def test_resolve_backend_default_auto_is_molscribe():
    # 主进程 torch 是 cpu 版（无 CUDA）→ auto 落到 molscribe
    assert ocsr.resolve_ocsr_backend() == "molscribe"


def test_resolve_backend_explicit(monkeypatch):
    monkeypatch.setenv("FORMUMIND_OCSR_BACKEND", "molscribe")
    get_settings.cache_clear()
    assert ocsr.resolve_ocsr_backend() == "molscribe"

    monkeypatch.setenv("FORMUMIND_OCSR_BACKEND", "decimer")
    get_settings.cache_clear()
    assert ocsr.resolve_ocsr_backend() == "decimer"


# ── degradation invariance（缺库 → 中性返回）────────────────────────────────


def test_molscribe_available_false_without_molscribe():
    assert ocsr.molscribe_available() is False


def test_decimer_available_false_without_decimer():
    assert ocsr.decimer_available() is False


def test_predict_smiles_molscribe_absent():
    assert ocsr.predict_smiles_molscribe("/tmp/x.png") is None


def test_predict_smiles_local_dispatch_absent():
    assert ocsr.predict_smiles_local("/tmp/x.png", backend="molscribe") is None
    assert ocsr.predict_smiles_local("/tmp/x.png", backend="decimer") is None
    assert ocsr.predict_smiles_local("/tmp/x.png", backend="unknown") is None


def test_prewarm_molscribe_noop_without_molscribe():
    assert ocsr.prewarm_molscribe() is False


def test_availability_structure():
    a = ocsr.availability()
    assert set(a) >= {
        "enabled", "backend", "ocsr_backend", "molscribe_installed",
        "decimer_installed", "molscribe_queue", "decimer_queue",
        "molscribe_timeout_s", "decimer_timeout_s",
    }
    assert a["molscribe_installed"] is False
    assert a["decimer_installed"] is False
    assert a["backend"] in ("molscribe", "decimer")


# ── config 字段 ──────────────────────────────────────────────────────────────


def test_settings_have_ocsr_fields():
    s = Settings()
    assert s.ocsr_backend == "auto"
    assert s.molscribe_queue == "molscribe"
    assert s.molscribe_timeout_s == 180.0


# ── vision_extract._ocsr_direct 投递路由 ─────────────────────────────────────


def test_ocsr_direct_routes_to_correct_queue(monkeypatch):
    """按 backend 选择正确的 task 名与队列（不真正发 Celery 任务）。"""
    from app.config import Settings as _Settings
    from app.services import vision_extract as ve

    captured: dict[str, str] = {}

    class _FakeResult:
        def get(self, timeout=None):
            return {"ok": True, "smiles": "CCO"}

    def _fake_send_task(name, args=None, queue=None):
        captured["name"] = name
        captured["queue"] = queue
        return _FakeResult()

    import app.worker.celery_app as ca

    monkeypatch.setattr(ca.celery_app, "send_task", _fake_send_task)

    s = _Settings(decimer_enabled=True, ocsr_backend="molscribe")
    out = ve._ocsr_direct(b"fake-png", s, "molscribe")
    assert captured["name"] == "formumind.molscribe_recognize"
    assert captured["queue"] == "molscribe"
    assert out is not None and out.molecules[0].smiles == "CCO"

    captured.clear()
    s2 = _Settings(decimer_enabled=True, ocsr_backend="decimer")
    ve._ocsr_direct(b"fake-png", s2, "decimer")
    assert captured["name"] == "formumind.decimer_recognize"
    assert captured["queue"] == "decimer"


def test_ocsr_direct_disabled_returns_none():
    from app.config import Settings as _Settings
    from app.services import vision_extract as ve

    s = _Settings(decimer_enabled=False)
    assert ve._ocsr_direct(b"fake-png", s, "molscribe") is None
