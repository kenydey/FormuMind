"""OpenAlex / Unpaywall mailto must default to None (configured via settings UI/API).

2026-10-11 决策：运营配置不硬编码，经设置界面/API 配置，Cheng 单独手动测试。
测试用邮箱（kenydey@gmail.com）仅用于编程测试，不进入生产代码。
"""
from __future__ import annotations

import os

import pytest

from app.config import Settings

# 测试专用邮箱（Cheng 提供，仅测试用，生产经设置界面配置）
_TEST_MAILTO = "kenydey@gmail.com"


def test_openalex_mailto_default_is_none():
    field = Settings.model_fields["openalex_mailto"]
    assert field.default is None


def test_unpaywall_mailto_default_is_none():
    field = Settings.model_fields["unpaywall_mailto"]
    assert field.default is None


def test_mailto_configurable_via_env(monkeypatch):
    """验证经环境变量可配置（模拟设置界面/API 写入）"""
    monkeypatch.setenv("FORMUMIND_OPENALEX_MAILTO", _TEST_MAILTO)
    monkeypatch.setenv("FORMUMIND_UNPAYWALL_MAILTO", _TEST_MAILTO)
    # 清除缓存后重读
    from app.config import get_settings
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.openalex_mailto == _TEST_MAILTO
        assert s.unpaywall_mailto == _TEST_MAILTO
    finally:
        get_settings.cache_clear()
        monkeypatch.undo()
