"""Wave C scholar_helpers: notice_kind + expand_citations + provenance."""
from __future__ import annotations

import httpx

from app.services import scholar_helpers as sh


def test_classify_notice_kinds():
    assert sh._classify_notice([], []) == "none"
    assert (
        sh._classify_notice([{"type": "retraction"}], []) == "retracted_work"
    )
    assert sh._classify_notice([{"type": "corrigendum"}], []) == "corrected"
    assert sh._classify_notice([{"type": "other"}], []) == "notice"


def test_verify_dois_notice_kind(monkeypatch):
    class Resp:
        status_code = 200

        def json(self):
            return {
                "message": {
                    "title": ["Demo paper"],
                    "update-to": [{"type": "retraction", "DOI": "10.1/x"}],
                    "updated-by": [],
                }
            }

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return Resp()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    rows = sh.verify_dois(["10.1000/xyz"], enabled=True)
    assert rows[0]["status"] == "ok"
    assert rows[0]["notice_kind"] == "retracted_work"
    assert rows[0]["retracted"] is True


def test_annotate_footer_retracted_work(monkeypatch):
    monkeypatch.setattr(
        sh,
        "verify_dois",
        lambda dois, **k: [
            {
                "doi": dois[0],
                "status": "ok",
                "title": "t",
                "retracted": True,
                "notice_kind": "retracted_work",
            }
        ],
    )
    text, results = sh.annotate_answer_dois("Claim with doi 10.1000/abc.")
    assert "疑似撤稿" in text
    assert results


def test_expand_citations_mock(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, timeout_s=6.0):
        calls["n"] += 1
        if "works/doi:" in url:
            return {"id": "https://openalex.org/W123"}
        if "cited_by:W123" in url:
            return {
                "results": [
                    {
                        "doi": "https://doi.org/10.1/ref",
                        "title": "Ref",
                        "publication_year": 2020,
                        "cited_by_count": 10,
                    }
                ]
            }
        if "cites:W123" in url:
            return {
                "results": [
                    {
                        "doi": "https://doi.org/10.1/fwd",
                        "title": "Fwd",
                        "publication_year": 2024,
                        "cited_by_count": 2,
                    }
                ]
            }
        return {}

    monkeypatch.setattr(sh, "_openalex_get", fake_get)
    out = sh.expand_citations("10.1000/seed", n_backward=5, n_forward=5)
    assert out["work_id"] == "W123"
    assert out["references"][0]["doi"] == "10.1/ref"
    assert out["cited_by"][0]["doi"] == "10.1/fwd"
    assert calls["n"] >= 3


def test_build_evidence_provenance():
    class Claim:
        def __init__(self, status):
            self.status = status

    prov = sh.build_evidence_provenance(
        doi_results=[
            {"doi": "10.1/a", "status": "not_found", "notice_kind": "none"},
            {"doi": "10.1/b", "status": "ok", "notice_kind": "retracted_work"},
        ],
        sourced_claims=[Claim("unsupported"), Claim("supported")],
        enabled=True,
    )
    assert prov is not None
    assert prov["evidence_availability"] in {"partial", "unavailable"}
    assert prov["notes"]
