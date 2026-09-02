"""get_experiment_store 的 auto 语义（P1）：与 campaign_store 对齐 —
Datalab 可达即以其为 SSOT；不可达时 REQUIRED 硬失败，否则回退 sqlite。"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.db import store as store_mod
from app.db.store import DatalabExperimentStore, SqlExperimentStore
from app.db.datalab_client import DatalabUnavailableError


@pytest.fixture(autouse=True)
def _reset_store_singleton():
    store_mod.reset_experiment_store(None)
    yield
    store_mod.reset_experiment_store(None)


def _settings(**overrides):
    return get_settings().model_copy(update=overrides)


def test_auto_reachable_uses_datalab(monkeypatch):
    """auto + Datalab 可达 → DatalabExperimentStore（修复前落 sqlite）。"""
    s = _settings(
        experiment_backend="auto",
        campaign_backend="auto",
        datalab_api_url="http://datalab.test:5001",
        datalab_required=False,
    )
    monkeypatch.setattr(store_mod, "check_datalab_reachable", lambda url, timeout: (True, None))
    store = store_mod.get_experiment_store(s)
    assert isinstance(store, DatalabExperimentStore)


def test_auto_unreachable_required_raises(monkeypatch):
    """auto + 不可达 + datalab_required → 硬失败（与 REQUIRED 语义一致）。"""
    s = _settings(
        experiment_backend="auto",
        campaign_backend="auto",
        datalab_api_url="http://datalab.test:5001",
        datalab_required=True,
    )
    monkeypatch.setattr(store_mod, "check_datalab_reachable", lambda url, timeout: (False, "conn refused"))
    with pytest.raises(DatalabUnavailableError):
        store_mod.get_experiment_store(s)


def test_auto_unreachable_campaign_datalab_raises(monkeypatch):
    """auto + 不可达 + campaign 显式 datalab → 硬失败（台账在 datalab，训练不可降级分裂）。"""
    s = _settings(
        experiment_backend="auto",
        campaign_backend="datalab",
        datalab_api_url="http://datalab.test:5001",
        datalab_required=False,
    )
    monkeypatch.setattr(store_mod, "check_datalab_reachable", lambda url, timeout: (False, "down"))
    with pytest.raises(DatalabUnavailableError):
        store_mod.get_experiment_store(s)


def test_auto_unreachable_optional_falls_back_sqlite(monkeypatch):
    """auto + 不可达 + 非必需 → 回退 SqlExperimentStore（保留兜底）。"""
    s = _settings(
        experiment_backend="auto",
        campaign_backend="sqlite",
        datalab_api_url="http://datalab.test:5001",
        datalab_required=False,
    )
    monkeypatch.setattr(store_mod, "check_datalab_reachable", lambda url, timeout: (False, "down"))
    store = store_mod.get_experiment_store(s)
    assert isinstance(store, SqlExperimentStore)


def test_explicit_sqlite_never_probes(monkeypatch):
    """显式 sqlite → 不探测直接落 SqlExperimentStore。"""
    s = _settings(
        experiment_backend="sqlite",
        datalab_api_url="http://datalab.test:5001",
    )
    called = False

    def _fail(url, timeout):  # pragma: no cover - 不应被调用
        nonlocal called
        called = True
        return (True, None)

    monkeypatch.setattr(store_mod, "check_datalab_reachable", _fail)
    store = store_mod.get_experiment_store(s)
    assert isinstance(store, SqlExperimentStore)
    assert called is False
