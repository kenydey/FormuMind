"""标题层级规则层测试(2026-09-05, 云 MinerU text_level 扁平化修复)."""
from app.services.hybrid_parse import _heading_markdown


def test_numbered_headings_get_exact_depth():
    # API 把 1./1.1/1.2 全标 level 2 —— 规则层应按编号段数定级
    text, level = _heading_markdown("1. Formulation Results", api_level=2)
    assert text == "1. Formulation Results" and level == 1
    text, level = _heading_markdown("1.1 Resin System A", api_level=2)
    assert level == 2
    text, level = _heading_markdown("1.2.3 Mixed Coating", api_level=0)
    assert level == 3
    # 中文顿号/全角编号也可
    text, level = _heading_markdown("1、引言", api_level=2)
    assert level == 1


def test_api_level_used_when_no_number_prefix():
    text, level = _heading_markdown("Formulation Results", api_level=2)
    assert text == "Formulation Results" and level == 2
    # 无编号无 api level → 非标题
    assert _heading_markdown("plain prose line", api_level=0) is None
    assert _heading_markdown("", api_level=2) is None
    assert _heading_markdown("   ", api_level=2) is None


def test_no_false_positive_on_year_or_amount_prose():
    # 正文数字开头(无句点/顿号)不得误伤为标题
    assert _heading_markdown("2026 年表面处理报告", api_level=0) is None
    assert _heading_markdown("40 份树脂与 60 份水", api_level=0) is None
    assert _heading_markdown("0.5% 硅烷偶联剂", api_level=0) is None
    # 编号后必须紧跟 . 、 或全角句点再加空白
    assert _heading_markdown("1 份树脂", api_level=0) is None
