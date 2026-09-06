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
    monkeypatch.setattr(notebooklm, "_notebooklm_available", lambda notebook_id=None: True)

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


def test_resolve_prefers_explicit_over_global(monkeypatch):
    monkeypatch.setenv("FORMUMIND_NOTEBOOKLM_NOTEBOOK_ID", "global-nb")
    _reset_settings()
    try:
        assert notebooklm._resolve_notebook_id("project-nb") == "project-nb"
        assert notebooklm._resolve_notebook_id("") == "global-nb"
        assert notebooklm._resolve_notebook_id(None) == "global-nb"
    finally:
        _reset_settings()


def test_available_with_project_id_without_global(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_NOTEBOOKLM_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_NOTEBOOKLM_NOTEBOOK_ID", "")
    session = tmp_path / "session.json"
    session.write_text("{}")
    monkeypatch.setenv("FORMUMIND_NOTEBOOKLM_STORAGE_PATH", str(session))
    _reset_settings()
    try:
        monkeypatch.setattr(notebooklm, "_lib_installed", lambda: True)
        # _auth_ready imports the lib itself; stub the whole gate.
        monkeypatch.setattr(notebooklm, "_auth_ready", lambda: True)
        assert notebooklm._notebooklm_available("proj-1") is True
        assert notebooklm._notebooklm_available() is False
    finally:
        _reset_settings()


def test_search_uses_explicit_notebook_id(monkeypatch):
    monkeypatch.setattr(notebooklm, "_notebooklm_available", lambda notebook_id=None: True)

    seen = {}

    async def fake_aquery(query, limit, *, notebook_id=None):
        seen["notebook_id"] = notebook_id
        seen["query"] = query
        return []

    monkeypatch.setattr(notebooklm, "_aquery", fake_aquery)
    monkeypatch.setattr(notebooklm, "_run_async", lambda coro: __import__("asyncio").run(coro))

    assert notebooklm.search_notebooklm("zinc primer", notebook_id="proj-nb") == []
    assert seen["notebook_id"] == "proj-nb"
    assert seen["query"] == "zinc primer"


def test_setup_status_auth_ready_without_global_notebook_id(monkeypatch, tmp_path):
    monkeypatch.setenv("FORMUMIND_NOTEBOOKLM_ENABLED", "true")
    monkeypatch.setenv("FORMUMIND_NOTEBOOKLM_NOTEBOOK_ID", "")
    session = tmp_path / "session.json"
    session.write_text("{}")
    monkeypatch.setenv("FORMUMIND_NOTEBOOKLM_STORAGE_PATH", str(session))
    _reset_settings()
    try:
        monkeypatch.setattr(notebooklm, "_lib_installed", lambda: True)
        st = notebooklm.get_setup_status()
        assert st["auth_ready"] is True
        assert st["available"] is True
        assert st["notebook_id_set"] is False
    finally:
        _reset_settings()
