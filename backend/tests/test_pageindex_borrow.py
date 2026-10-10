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
