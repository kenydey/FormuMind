"""P2: 解析截断提示（parse_notices）—— 用户可见，不再只记日志。"""
from __future__ import annotations


def test_collect_drains_to_caller():
    from app.services.parse_notices import collect, note

    with collect() as buf:
        note("第 1 页起共 120 页，仅解析前 50 页")
        note("第 1 页起共 120 页，仅解析前 50 页")  # 去重
        note("OCR 兜底：第 3 页")
    assert buf == ["第 1 页起共 120 页，仅解析前 50 页", "OCR 兜底：第 3 页"]


def test_note_noop_outside_collect():
    from app.services.parse_notices import note

    note("无人收集时不抛错")  # 不应抛错


def test_nested_collect_isolated():
    from app.services.parse_notices import collect, note

    with collect() as outer:
        note("outer")
        with collect() as inner:
            note("inner")
        assert inner == ["inner"]
        note("outer-2")
    assert outer == ["outer", "outer-2"]


def test_ingest_file_surfaces_parse_warnings(monkeypatch):
    """ingest_file 的 IngestOutcome.warnings 带出解析提示。"""
    from app.services import ingestion as ing
    from app.services.parse_notices import note

    class _FakeParsed:
        markdown = "有效文本 " * 50
        tables = []
        structured = None

    def fake_parse_document(content: bytes, kind: str):
        note("测试截断提示：仅解析前 10 页")
        return _FakeParsed()

    monkeypatch.setattr("app.services.parsing.parse_document", fake_parse_document)
    out = ing.ingest_file("doc.pdf", b"%PDF-1.4 fake", persist=False)
    assert any("截断" in w for w in out.warnings), out.warnings
