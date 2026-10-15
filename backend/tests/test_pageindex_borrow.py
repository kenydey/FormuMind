"""PageIndex 借鉴方案测试：页码产生 + 源文件新页面打开。"""
import tempfile
from pathlib import Path

import app.services.pdf_local as pdf_local
from app.services.parsing import _inject_approximate_page_markers


def _long_text(lines=6):
    return ("这是测试文本行，用于验证页码注入功能。" * 20 + "\n") * lines


def test_inject_markers_three_pages():
    orig = pdf_local.page_count
    pdf_local.page_count = lambda c: 3  # noqa: E731
    try:
        out = _inject_approximate_page_markers(b"x", _long_text())
        markers = [l for l in out.splitlines() if "page:" in l]
        assert markers == ["<!-- page:1 -->", "<!-- page:2 -->", "<!-- page:3 -->"]
    finally:
        pdf_local.page_count = orig


def test_inject_skips_existing_markers():
    text = "<!-- page:1 -->\n已有页码\n"
    assert _inject_approximate_page_markers(b"x", text) == text


def test_inject_fail_open():
    # 页数获取失败 → 原文返回
    orig = pdf_local.page_count
    def boom(c):
        raise RuntimeError("nope")
    pdf_local.page_count = boom
    try:
        text = _long_text()
        assert _inject_approximate_page_markers(b"x", text) == text
    finally:
        pdf_local.page_count = orig


def test_inject_short_text_untouched():
    text = "太短"
    assert _inject_approximate_page_markers(b"x", text) == text


def test_markers_survive_chunker():
    orig = pdf_local.page_count
    pdf_local.page_count = lambda c: 2  # noqa: E731
    try:
        out = _inject_approximate_page_markers(b"x", _long_text())
        from app.services.chunking import chunk_markdown
        pages = sorted({c.page_no for c in chunk_markdown(out)})
        assert pages == [1, 2]
    finally:
        pdf_local.page_count = orig


def test_prompt_has_page_citation_rule():
    from app.services.llm import _chat_prompt
    prompt = _chat_prompt(question="测试", evidence=[], domain=None)
    assert "Page citation rule" in prompt
    assert "(p.N)" in prompt


def test_source_files_persist_find():
    from app.services.source_files import find_source_file, persist_source_file
    tmp = Path(tempfile.mkdtemp()) / "doc.pdf"
    tmp.write_bytes(b"%PDF-1.4 fake")
    dest = persist_source_file("pytest_src_abc", tmp, "doc.pdf")
    assert dest is not None
    assert find_source_file("pytest_src_abc") == dest
    dest.unlink()


def test_source_file_endpoint():
    import tempfile as tf
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services.source_files import persist_source_file
    tmp = Path(tf.mkdtemp()) / "d.pdf"
    tmp.write_bytes(b"%PDF-1.4 fake")
    dest = persist_source_file("pytest_ep_1", tmp, "d.pdf")
    assert dest is not None
    c = TestClient(app)
    r = c.get("/api/documents/pytest_ep_1/file")
    assert r.status_code == 200
    assert "inline" in r.headers["content-disposition"]
    assert r.headers["content-type"] == "application/pdf"
    assert c.get("/api/documents/pytest_ep_missing/file").status_code == 404
    dest.unlink()


# ── A1: 编号标题规则扩展 ──

def test_heading_roman_cjk_letter_chapter():
    from app.services.hybrid_parse import _heading_markdown
    assert _heading_markdown("IV. 实验结果", 0)[1] == 1
    assert _heading_markdown("一、引言", 0)[1] == 1
    assert _heading_markdown("a. 试剂准备", 0)[1] == 1
    assert _heading_markdown("Chapter 3 方法", 0)[1] == 1
    assert _heading_markdown("附录 A 数据表", 0)[1] == 1
    # 防误伤
    assert _heading_markdown("2026 年市场分析", 0) is None
    assert _heading_markdown("0.5% 硅烷偶联剂", 0) is None
    # 原有规则不受影响
    assert _heading_markdown("1.2.3 合成步骤", 0)[1] == 3
    assert _heading_markdown("2. 测试方法", 0)[1] == 1


def test_apply_heading_rules_local():
    from app.services.hybrid_parse import _apply_heading_rules_local
    md = "# 已有标题\n\nIV. 实验结果\n\n正文。\n\n2026 年市场分析\n"
    out = _apply_heading_rules_local(md)
    assert "# IV. 实验结果" in out
    assert "# 已有标题" in out  # 不重复加
    assert "2026 年市场分析" in out and "# 2026" not in out


# ── A2: 书签 → heading_path ──

def test_bookmarks_to_page_map():
    from app.services.parsing import _bookmarks_to_page_map
    bm = [("第一章", 0, 1), ("1.1 节", 1, 3), ("第二章", 0, 5)]
    m = _bookmarks_to_page_map(bm, 6)
    assert m[1] == "第一章" and m[2] == "第一章"
    assert m[3] == "第一章 > 1.1 节" and m[4] == "第一章 > 1.1 节"
    assert m[5] == "第二章" and m[6] == "第二章"


def test_read_pdf_bookmarks_fail_open():
    from app.services.parsing import _read_pdf_bookmarks
    # 非 PDF / 损坏内容 → []（fail-open）
    assert _read_pdf_bookmarks(b"not a pdf") == []


def test_chunk_markdown_bookmark_fill():
    from app.services.chunking import chunk_markdown
    md = "<!-- page:1 -->\n\n第一章内容。" + "正文。" * 100 + "\n\n<!-- page:2 -->\n\n第二页内容。" + "正文。" * 100
    chunks = chunk_markdown(md, bookmarks=[("绪论", 0, 1), ("方法", 0, 2)])
    assert chunks
    assert all(c.heading_path for c in chunks), [c.heading_path for c in chunks]
    assert chunks[0].heading_path == "绪论"


# ── A5: page_end ──

def test_chunk_page_end_tracking():
    """A chunk reports the page(s) its own text is on - not the range of the section it was accumulated in."""
    from app.services.chunking import chunk_markdown
    md = (
        "<!-- page:1 -->\n\n# 第一章\n\n" + "正文内容。" * 300
        + "\n\n<!-- page:2 -->\n\n" + "第二页内容。" * 300
        + "\n\n<!-- page:3 -->\n\n" + "第三页内容。" * 50
    )
    chunks = chunk_markdown(md, max_chars=1600)
    by_text = {"正文内容": 1, "第二页内容": 2, "第三页内容": 3}
    for c in chunks:
        expected = next(page for marker, page in by_text.items() if marker in c.text)
        assert c.page_no == expected, (c.page_no, expected, c.text[:12])
        assert c.page_end is None  # none of these blocks crosses a page boundary
    # every chunk's range is well formed
    assert all(c.page_end is None or c.page_end >= c.page_no for c in chunks)


def test_blocks_under_one_heading_keep_their_own_pages():
    from app.services.chunking import chunk_markdown, page_marker

    md = f"# H\n\n{page_marker(1)}\n\nalpha.\n\n{page_marker(2)}\n\nbeta.\n\n{page_marker(3)}\n\ngamma."
    assert [(c.text, c.page_no, c.page_end) for c in chunk_markdown(md, max_chars=400, overlap=0)] == [
        ("alpha.", 1, None), ("beta.", 2, None), ("gamma.", 3, None),
    ]


def test_page_ranges_are_never_inverted_for_increasing_page_markers():
    import random

    from app.services.chunking import chunk_markdown, page_marker

    rng = random.Random(5)
    for _ in range(60):
        parts, page = [], 0
        for _ in range(rng.randint(2, 14)):
            r = rng.random()
            if r < 0.25:
                page += rng.randint(1, 2)
                parts.append(page_marker(page))
            elif r < 0.4:
                parts.append("## " + "标题" * rng.randint(1, 4))
            elif r < 0.55:
                parts.append("| a | b |\n| --- | --- |\n| 1 | 2 |")
            else:
                parts.append("正文" * rng.choice([5, 80, 700]))
        for c in chunk_markdown("\n\n".join(parts), max_chars=600, overlap=0):
            assert c.page_end is None or c.page_no is None or c.page_end >= c.page_no, (c.page_no, c.page_end)


# ── A4: page_citation_honesty ──

def test_page_citation_honesty():
    from app.evals.rigor_rubric import metric_page_citation_honesty as m
    assert m("表干 2h[^1] (p.3)。", [{"page": 3}])["score"] == 1.0
    r = m("通过盐雾[^1] (p.9)。", [{"page": 2}])
    assert r["score"] == 0.0 and "不符" in r["failures"][0]["reason"]
    r = m("浓度 0.5%[^1] (p.4)。", [{"title": "x"}])
    assert r["score"] == 0.0 and "编造" in r["failures"][0]["reason"]
    r = m("表干 2h[^1]。", [{"page": 3}])
    assert r.get("not_applicable") is True
