"""P1-3: ingest_url 遇到 PDF 必须走 parse_document(body, "pdf")，
禁止把 PDF 裸字节 latin-1 解码后入库（数据污染）。

同时覆盖 P2 URL 下载上限：stream=True 真正流式，超限即停。
"""
from __future__ import annotations

import pytest

from app.services.ingestion import ingest_url


@pytest.fixture(autouse=True)
def _sanitize_no_proxy(monkeypatch):
    """沙箱 no_proxy 含括号 IPv6（`[::1]`），httpx 构造 Client 即炸。
    清洗后 httpx.Client 才能正常构造（见 AGENTS.md）。"""
    import os

    for key in ("no_proxy", "NO_PROXY"):
        raw = os.environ.get(key, "")
        if raw:
            monkeypatch.setenv(
                key, ",".join(p for p in raw.split(",") if "[" not in p)
            )


class _FakeStreamResp:
    def __init__(self, body: bytes, content_type: str = "application/pdf"):
        self._body = body
        self.headers = {"content-type": content_type}
        self.is_redirect = False
        self.closed = False

    def raise_for_status(self):
        pass

    def iter_bytes(self, chunk_size: int = 65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    def close(self):
        self.closed = True


class _FakeParsed:
    def __init__(self, markdown: str):
        self.markdown = markdown
        self.tables = []
        self.structured = None


def _mock_pdf_download(monkeypatch, body: bytes, content_type: str = "application/pdf"):
    import httpx

    import app.services.ingestion as ing

    # 沙箱无 DNS：跳过 SSRF 安全检查（被测的是解析路由，不是 URL 校验）
    monkeypatch.setattr(ing, "_is_safe_url", lambda url: True)

    calls: dict = {}

    def fake_send(self, request, **kwargs):
        calls["stream"] = kwargs.get("stream")
        calls["url"] = str(request.url)
        return _FakeStreamResp(body, content_type)

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    return calls


def test_ingest_url_pdf_routes_to_parse_document(monkeypatch):
    """content-type=pdf → parse_document(body, "pdf") 被调用，markdown 入库。"""
    import app.services.ingestion as ing

    pdf_bytes = b"%PDF-1.4 fake binary \xff\xfe content"
    send_calls = _mock_pdf_download(monkeypatch, pdf_bytes)

    parsed_calls: dict = {}

    def fake_parse_document(body: bytes, kind: str):
        parsed_calls["body"] = body
        parsed_calls["kind"] = kind
        return _FakeParsed("# Fake PDF Title\n\n表格内容 " * 20)

    monkeypatch.setattr("app.services.parsing.parse_document", fake_parse_document)

    out = ingest_url("https://example.com/doc.pdf", persist=False)

    assert send_calls["stream"] is True  # 风险3：真正流式
    assert parsed_calls["kind"] == "pdf"
    assert parsed_calls["body"] == pdf_bytes  # 裸字节原样传入，非 latin-1 解码
    # persist=False 时 source guide 跳过，status=skipped 属预期；关键是 evidence 非空
    assert out.evidence, "解析出的 markdown 应生成 evidence"
    assert "Fake PDF Title" in out.evidence[0].snippet


def test_ingest_url_pdf_detected_by_magic_number(monkeypatch):
    """content-type 撒谎（octet-stream）时靠 %PDF 魔数判 PDF。"""
    import app.services.ingestion as ing

    pdf_bytes = b"%PDF-1.7 binary \x00\x01\x02"
    _mock_pdf_download(monkeypatch, pdf_bytes, content_type="application/octet-stream")

    kinds: list = []

    def fake_parse_document(body: bytes, kind: str):
        kinds.append(kind)
        return _FakeParsed("pdf text " * 20)

    monkeypatch.setattr("app.services.parsing.parse_document", fake_parse_document)

    out = ingest_url("https://example.com/download?id=1", persist=False)
    assert kinds == ["pdf"]
    assert out.evidence


def test_ingest_url_pdf_empty_parse_skipped(monkeypatch):
    """PDF 解析出空文本 → skipped，不入库乱码。"""
    import app.services.ingestion as ing

    _mock_pdf_download(monkeypatch, b"%PDF-1.4 empty")

    def fake_parse_document(body: bytes, kind: str):
        return _FakeParsed("   ")

    monkeypatch.setattr("app.services.parsing.parse_document", fake_parse_document)

    out = ingest_url("https://example.com/empty.pdf", persist=False)
    assert out.extraction_status == "skipped"
    # 空解析返回一条"无法提取"提示 evidence，而非乱码入库
    assert len(out.evidence) == 1
    assert "无法从该 URL 提取文本" in out.evidence[0].snippet


def test_ingest_url_enforces_max_bytes_streaming(monkeypatch):
    """超限响应在流式读取中被截断（不先全载入内存）。"""
    import app.services.ingestion as ing

    big = b"x" * (3 * 1024 * 1024)  # 3 MiB
    _mock_pdf_download(monkeypatch, big, content_type="text/html")

    monkeypatch.setattr(ing.get_settings(), "ingest_max_url_bytes", 1024 * 1024)
    out = ingest_url("https://example.com/big.html", persist=False)
    assert out.extraction_status == "skipped"
    assert "超限" in out.evidence[0].snippet


def test_v7_ingest_url_rejects_unrecognized_binary(monkeypatch):
    """v7 解析-1: 无法识别的二进制 URL 拒绝入库，不 latin-1 裸解码写乱码。"""
    # 伪造 zip 包头但扩展名/content-type 都无法识别
    fake_zip = b"PK\x03\x04" + b"\x00" * 100
    _mock_pdf_download(monkeypatch, fake_zip, content_type="application/octet-stream")
    out = ingest_url("https://example.com/file.unknownbin", persist=False)
    assert out.extraction_status == "skipped"
    assert "不支持" in out.evidence[0].snippet
