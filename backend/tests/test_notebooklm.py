"""Offline-fallback tests for the NotebookLM retrieval adapter.

The unofficial ``notebooklm-py`` SDK is optional and disabled by default, so the
adapter must degrade silently to ``[]`` whenever the feature is off, the login
session is missing, or a query raises. When forced "available", the chat result
must map cleanly into Evidence objects tagged ``source="NotebookLM"``.
"""
from app.config import get_settings
from app.services import notebooklm


def _reset_settings():
    get_settings.cache_clear()


def test_disabled_by_default():
    _reset_settings()
    assert notebooklm._notebooklm_available() is False
    assert notebooklm.search_notebooklm("epoxy resin") == []


def test_enabled_but_no_session_file(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_NOTEBOOKLM_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_NOTEBOOKLM_NOTEBOOK_ID", "nb-123")
    monkeypatch.setenv(
        "FORMUMIND_NOTEBOOKLM_STORAGE_PATH", str(tmp_path / "missing.json")
    )
    _reset_settings()
    try:
        # Session file absent → unavailable → empty, never raises.
        assert notebooklm._notebooklm_available() is False
        assert notebooklm.search_notebooklm("epoxy resin") == []
    finally:
        _reset_settings()


def test_query_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(notebooklm, "_notebooklm_available", lambda: True)

    def _boom(coro):
        # Close the coroutine to avoid "never awaited" warnings, then fail.
        coro.close()
        raise RuntimeError("bridge down")

    monkeypatch.setattr(notebooklm, "_run_async", _boom)
    assert notebooklm.search_notebooklm("epoxy resin") == []


def test_to_evidence_maps_answer():
    class _Result:
        answer = "Zinc phosphate primers reach 500h salt spray."
        citations = []

    ev = notebooklm._to_evidence(_Result(), "anticorrosion primer", limit=5)
    assert len(ev) == 1
    assert ev[0].source == "NotebookLM"
    assert "Zinc phosphate" in ev[0].snippet
    assert 0.0 <= ev[0].relevance <= 1.0


def test_to_evidence_maps_citations():
    class _Cite:
        def __init__(self, t, txt):
            self.title = t
            self.text = txt

    class _Result:
        answer = "summary"
        citations = [_Cite("Patent A", "claim text A"), _Cite("Patent B", "claim text B")]

    ev = notebooklm._to_evidence(_Result(), "q", limit=5)
    assert len(ev) == 2
    assert all(e.source == "NotebookLM" for e in ev)
    assert ev[0].title == "Patent A"
    assert ev[0].relevance >= ev[1].relevance
