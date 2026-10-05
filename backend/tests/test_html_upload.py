"""An uploaded HTML page is converted, not stored as source (round-4).

URL ingestion runs pages through ``html_to_markdown``; the upload path — ``.html`` / ``.htm`` are in the upload
dialog's accept list — took the text tier, which only decodes the bytes. The chunk store then held
``<!doctype html><html><head><style>body{color:red}</style><script>var x=1;</script>…`` as "text". Without
trafilatura (an optional extra) the converter's fallback also flattened a table into one line of words.
"""
from __future__ import annotations

import pytest

from app.services import parsing

PAGE = """<!doctype html><html><head><title>环氧底漆技术资料</title>
<style>body{color:red}</style><script>var tracking = 1;</script></head>
<body><nav>首页 | 产品 | 联系我们</nav><h1>环氧锌磷酸盐底漆</h1>
<p>本产品为双组分环氧底漆，<b>耐盐雾性能</b>可达 720 小时，适用于钢结构防腐，施工前需充分搅拌均匀。</p>
<table><tr><th>项目</th><th>指标</th><th>单位</th></tr>
<tr><td>固体含量</td><td>65</td><td>%</td></tr>
<tr><td>粘度</td><td>1200</td><td>mPa·s</td></tr></table>
<footer>版权所有</footer></body></html>""".encode("utf-8")


@pytest.mark.parametrize("ext", ["html", "htm"])
def test_an_uploaded_page_is_converted(ext):
    result = parsing.parse_document(PAGE, ext)
    text = result.markdown
    assert "耐盐雾性能" in text and "720" in text
    for markup in ("<html", "<script", "<style", "<h1>", "<p>", "<b>", "tracking", "color:red"):
        assert markup not in text, f"{markup!r} survived: {text[:200]!r}"


def test_its_table_survives_as_a_table():
    text = parsing.parse_document(PAGE, "html").markdown
    assert "| 固体含量 | 65 | % |" in text.replace("  ", " ") or "固体含量 | 65" in text, text
    assert "---" in text, "a pipe table needs its separator row"


def test_the_converted_table_is_extracted_as_an_asset():
    result = parsing.parse_document(PAGE, "html")
    assert len(result.tables) == 1
    (table,) = result.tables
    assert table.headers == ["项目", "指标", "单位"]
    assert table.rows[0] == ["固体含量", "65", "%"]


def test_the_fallback_keeps_tables_when_trafilatura_is_absent(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "trafilatura", None)  # "not installed"
    text = parsing.html_to_markdown(PAGE.decode("utf-8"))
    assert "固体含量 | 65 | %" in text
    assert "<table" not in text and "<td>" not in text


def test_a_cell_with_a_pipe_does_not_break_the_row():
    html = "<table><tr><th>a</th><th>b</th></tr><tr><td>x|y</td><td>1</td></tr></table>"
    import sys

    sys.modules["trafilatura"] = None
    try:
        text = parsing.html_to_markdown(html)
    finally:
        del sys.modules["trafilatura"]
    assert "x\\|y" in text


def test_a_ragged_table_is_padded():
    html = "<table><tr><th>a</th><th>b</th><th>c</th></tr><tr><td>1</td></tr></table>"
    import sys

    sys.modules["trafilatura"] = None
    try:
        text = parsing.html_to_markdown(html)
    finally:
        del sys.modules["trafilatura"]
    assert "| 1 | | |" in text  # the converter collapses runs of spaces; the row still has three cells


def test_plain_text_uploads_are_untouched():
    assert parsing.parse_document("a < b and c > d".encode(), "txt").markdown == "a < b and c > d"
    assert parsing.parse_document(b"# T\n\n<b>x</b>", "md").markdown == "# T\n\n<b>x</b>"


def test_a_page_with_no_text_is_empty_not_raw_markup():
    result = parsing.parse_document(b"<html><head><script>x()</script></head><body></body></html>", "html")
    assert not result.markdown.strip()


# ── with trafilatura installed (the preferred converter) ────────────────────

def test_a_small_datasheet_page_keeps_its_table_whichever_converter_is_installed():
    """trafilatura drops to a plain-text baseline on a small page and puts every cell on its own line; a data
    table that came out without a pipe row must still reach the chunker as a table."""
    pytest.importorskip("trafilatura")
    text = parsing.html_to_markdown(PAGE.decode("utf-8"))
    assert "固体含量 | 65 | %" in text, text


def test_a_layout_table_is_left_to_trafilatura(monkeypatch):
    layout = "<table><tr><td>导航</td><td>正文</td></tr></table>"
    assert not parsing._has_data_table(layout)
    assert not parsing._has_data_table("<table><tr><td>只有一行</td><td>两列</td></tr></table>")
    assert parsing._has_data_table(PAGE.decode("utf-8"))


def test_a_realistic_article_page_keeps_trafilatura_output():
    pytest.importorskip("trafilatura")
    para = "<p>环氧锌磷酸盐底漆是一种双组分防腐底漆，由环氧树脂、聚酰胺固化剂、锌磷酸盐防锈颜料和填料组成，适用于碳钢表面的防腐保护，施工方便，附着力优异。</p>"
    page = (
        f"<html><body><header><nav><a href='/'>首页</a></nav></header><article><h1>技术数据表</h1>{para * 3}"
        f"<h2>典型性能</h2>{para}<table><tr><th>项目</th><th>指标</th></tr><tr><td>粘度</td><td>1200</td></tr></table>"
        f"{para * 2}</article><footer>版权所有</footer></body></html>"
    )
    text = parsing.html_to_markdown(page)
    assert "# 技术数据表" in text, "trafilatura's heading structure is kept"
    assert "| 粘度 | 1200 |" in text
    assert "版权所有" not in text, "boilerplate removal is kept"
