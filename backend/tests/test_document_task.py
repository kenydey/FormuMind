"""document_task 统一摄取入口测试(2026-09-05 P1)."""
import pytest

from app.services.document_task import resolve_document


def test_rejects_bad_doc_type():
    with pytest.raises(ValueError, match="doc_type"):
        resolve_document("patentt", "CN104561970A")


def test_rejects_empty_identifier():
    with pytest.raises(ValueError, match="identifier"):
        resolve_document("paper", "  ")


def test_patent_routes_to_fetch_and_ingest(monkeypatch):
    """patent → _fetch_patent_text → ingest_text → source_id/evidence."""
    from app.services import document_task as dt

    fetched = []
    calls = {}

    def fake_fetch_patent(ev, timeout):
        fetched.append((ev.identifier, timeout))
        return "# US1234567\n\nclaims text " * 30

    def fake_ingest_text(text, title, *, persist=True):
        calls["title"] = title
        calls["persist"] = persist
        return type(
            "O",
            (),
            {
                "evidence": [{"snippet": text[:100]}],
                "source_id": "src-1",
                "source_guide": None,
                "extraction_status": "ok",
            },
        )()

    monkeypatch.setattr("app.services.fulltext_fetcher._fetch_patent_text", fake_fetch_patent)
    monkeypatch.setattr("app.services.ingestion.ingest_text", fake_ingest_text)

    out = resolve_document("patent", "us1234567")
    assert out.error is None
    assert fetched[0][0] == "US1234567"  # 大写归一
    assert calls["title"] == "us1234567"
    assert calls["persist"] is True
    assert out.source_id == "src-1"
    assert out.tier == "dom"


def test_paper_fetch_failure_surfaces_reason(monkeypatch):
    from app.services import document_task as dt
    from app.services.fulltext_fetcher import FetchError

    def boom(ev, timeout):
        raise FetchError("OA 全文获取失败: status:403")

    monkeypatch.setattr("app.services.fulltext_fetcher._fetch_literature_text", boom)

    out = resolve_document("paper", "10.3390/ma15238676")
    assert out.error == "OA 全文获取失败: status:403"
    assert out.evidence == []


def test_web_requires_http_url():
    with pytest.raises(ValueError, match="http"):
        resolve_document("web", "not-a-url")
