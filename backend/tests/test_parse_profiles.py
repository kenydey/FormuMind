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
    assert "FORMUMIND_PDF_OCR" not in env, "that toggle had no reader (the local magic-pdf path it served is retired)"
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


def test_apply_high_is_all_local_and_pins_no_parser(monkeypatch):
    monkeypatch.setattr(pp.secrets_store, "write_env_updates", lambda d: None)
    monkeypatch.setattr(pp, "probe_availability", lambda: {"ok": True})
    result = pp.apply_profile("high")
    env = result["env"]
    # It used to pin FORMUMIND_PDF_PARSER=mineru ("local magic-pdf"). That path is retired, and pinning a tier also
    # drops every tier above it - so choosing the best profile made PDF parsing skip hybrid / Docling / marker.
    assert env["FORMUMIND_PDF_PARSER"] == "auto"
    assert env["FORMUMIND_GPU_ENABLED"] == "true"
    assert env["FORMUMIND_MINERU_ENABLED"] == "false"  # nothing is uploaded
    assert env["FORMUMIND_PDF_LOCAL_OCR"] == "true"


@pytest.mark.parametrize("profile", ["low", "mid", "cloud", "high"])
def test_every_profile_is_recognised_again_after_it_is_applied(monkeypatch, profile):
    monkeypatch.setattr(pp.secrets_store, "write_env_updates", lambda d: None)
    monkeypatch.setattr(pp, "probe_availability", lambda: {"ok": True})
    assert pp.apply_profile(profile)["profile"] == profile
    assert pp.current_profile() == profile


@pytest.mark.parametrize("profile", ["low", "mid", "cloud", "high"])
def test_no_profile_costs_the_pdf_cascade_its_best_local_parsers(monkeypatch, profile):
    """The consequence that mattered: whatever profile is chosen, hybrid still leads the PDF tiers."""
    from app.config import get_settings
    from app.services import parsing

    monkeypatch.setattr(pp.secrets_store, "write_env_updates", lambda d: None)
    monkeypatch.setattr(pp, "probe_availability", lambda: {"ok": True})
    pp.apply_profile(profile)
    names = [n for n, _ in parsing._pdf_tier_order(get_settings().pdf_parser)]
    assert names[:3] == ["hybrid", "docling", "marker"], (profile, names)


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
