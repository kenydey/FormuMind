"""OpenAlex / Unpaywall mailto must default to None (configured via settings UI/API).

2026-10-11 决策：运营配置不硬编码，经设置界面/API 配置，Cheng 单独手动测试。
"""
from __future__ import annotations

from app.config import Settings


def test_openalex_mailto_default_is_none():
    field = Settings.model_fields["openalex_mailto"]
    assert field.default is None


def test_unpaywall_mailto_default_is_none():
    field = Settings.model_fields["unpaywall_mailto"]
    assert field.default is None
