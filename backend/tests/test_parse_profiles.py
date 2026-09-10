"""parse-profiles 四档配置测试(2026-09-05; +cloud 2026-09-10)."""
import os

import pytest

from app.services import parse_profiles as pp

# apply_profile 直接写 os.environ —— autouse 清理, 防污染同批其他测试
_ENV_KEYS = [
    "FORMUMIND_GPU_ENABLED",
    "FORMUMIND_MINERU_ENABLED",
    "FORMUMIND_MINERU_BATCH_ENABLED",
    "FORMUMIND_PDF_LOCAL_OCR",
    "FORMUMIND_PDF_OCR",
    "FORMUMIND_RAPIDOCR_ENABLED",
    "FORMUMIND_PDF_PARSER",
    "FORMUMIND_RAG_BACKEND",
]


@pytest.fixture(autouse=True)
def _clean_profile_env(monkeypatch):
    # apply_profile 直接写 os.environ —— 用 setenv 纳入 monkeypatch 管理,
    # teardown 自动还原快照值; 另清 Settings 缓存防同批其他测试读脏实例。
    for k in _ENV_KEYS:
        monkeypatch.setenv(k, os.environ.get(k, ""))
    yield
    from app.config import get_settings

    get_settings.cache_clear()


def test_profiles_cover_four_names():
    assert set(pp.PROFILE_NAMES) == {"low", "mid", "cloud", "high"}
    assert pp.PROFILE_NAMES == ("low", "mid", "cloud", "high")


def test_apply_low_disables_gpu_and_cloud(monkeypatch):
    monkeypatch.setattr(pp.secrets_store, "write_env_updates", lambda d: None)
    monkeypatch.setattr(pp, "probe_availability", lambda: {"ok": True})
    result = pp.apply_profile("low")
    env = result["env"]
    assert env["FORMUMIND_GPU_ENABLED"] == "false"
    assert env["FORMUMIND_MINERU_ENABLED"] == "false"
    assert env["FORMUMIND_PDF_PARSER"] == "auto"
    assert env["FORMUMIND_PDF_OCR"] == "true"
    assert result["profile"] == "low"


def test_apply_cloud_disables_local_ocr_enables_mineru_batch(monkeypatch):
    monkeypatch.setattr(pp.secrets_store, "write_env_updates", lambda d: None)
    monkeypatch.setattr(pp, "probe_availability", lambda: {"ok": True})
    result = pp.apply_profile("cloud")
    env = result["env"]
    assert env["FORMUMIND_MINERU_ENABLED"] == "true"
    assert env["FORMUMIND_RAPIDOCR_ENABLED"] == "false"
    assert env["FORMUMIND_MINERU_BATCH_ENABLED"] == "true"
    assert env["FORMUMIND_PDF_LOCAL_OCR"] == "false"
    assert env["FORMUMIND_GPU_ENABLED"] == "false"
    assert env["FORMUMIND_PDF_PARSER"] == "auto"
    assert result["profile"] == "cloud"


def test_apply_high_pins_mineru_and_gpu(monkeypatch):
    monkeypatch.setattr(pp.secrets_store, "write_env_updates", lambda d: None)
    monkeypatch.setattr(pp, "probe_availability", lambda: {"ok": True})
    result = pp.apply_profile("high")
    env = result["env"]
    assert env["FORMUMIND_PDF_PARSER"] == "mineru"
    assert env["FORMUMIND_GPU_ENABLED"] == "true"
    assert env["FORMUMIND_MINERU_ENABLED"] == "false"  # 本地取代云
    assert env["FORMUMIND_PDF_LOCAL_OCR"] == "true"


def test_apply_rejects_unknown_profile():
    with pytest.raises(ValueError, match="profile"):
        pp.apply_profile("ultra")


def test_apply_sets_live_os_environ(monkeypatch):
    monkeypatch.setattr(pp.secrets_store, "write_env_updates", lambda d: None)
    monkeypatch.setattr(pp, "probe_availability", lambda: {"ok": True})
    import os

    os.environ.pop("FORMUMIND_GPU_ENABLED", None)
    pp.apply_profile("low")
    assert os.environ.get("FORMUMIND_GPU_ENABLED") == "false"


def test_current_profile_cloud_when_mineru_on_rapidocr_off(monkeypatch):
    monkeypatch.setattr(pp.secrets_store, "write_env_updates", lambda d: None)
    monkeypatch.setattr(pp, "probe_availability", lambda: {"ok": True})
    pp.apply_profile("cloud")
    assert pp.current_profile() == "cloud"


def test_current_profile_mid_when_mineru_and_rapidocr_on(monkeypatch):
    monkeypatch.setattr(pp.secrets_store, "write_env_updates", lambda d: None)
    monkeypatch.setattr(pp, "probe_availability", lambda: {"ok": True})
    pp.apply_profile("mid")
    assert pp.current_profile() == "mid"
