"""Tests for v0.3 endpoints: multi-source search, ingest, chat, settings."""
from fastapi.testclient import TestClient

from app.main import app
from app.services import ingestion, literature
from app.services.notebooklm import get_setup_status

client = TestClient(app)

_REQUIREMENT = {
    "domain": "anticorrosion_coating",
    "substrate": "carbon_steel",
    "salt_spray_hours": 500,
    "film_weight_gsm": 70,
    "cure_temperature_c": 80,
    "cleaning_efficiency": 90,
    "voc_limit_gpl": 420,
    "ph_target": None,
    "notes": "",
    "objectives": [],
}


def test_settings_lists_all_providers():
    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    ids = {p["id"] for p in body["providers"]}
    # All nine supported providers must be present.
    assert {
        "anthropic", "openai", "gemini", "xai", "groq",
        "deepseek", "qwen", "moonshot", "minimax",
    } <= ids
    # Each provider exposes at least one model, exactly one marked recommended.
    for p in body["providers"]:
        assert p["models"]
        assert sum(1 for m in p["models"] if m.get("recommended")) == 1


def test_settings_update_switches_provider():
    r = client.post("/api/settings", json={"provider": "deepseek", "model": "deepseek-v4-pro"})
    assert r.status_code == 200
    assert r.json()["provider"] == "deepseek"
    # Restore default so other tests are unaffected.
    client.post("/api/settings", json={"provider": "anthropic"})


def test_settings_accepts_camelcase_base_url(monkeypatch):
    from app.config import get_settings
    from app.services import llm as llm_mod

    get_settings.cache_clear()
    monkeypatch.setattr(
        llm_mod,
        "test_connection",
        lambda: {"ok": True, "provider": "deepseek", "model": "deepseek-v4-pro", "message": "连接成功"},
    )
    client.post(
        "/api/settings",
        json={
            "provider": "deepseek",
            "model": "deepseek-v4-pro",
            "api_key": "sk-test",
            "baseUrl": "https://api.deepseek.com",
        },
    )
    s = get_settings()
    assert s.llm_base_url == "https://api.deepseek.com"
    assert s.deepseek_api_key == "sk-test"
    get_settings.cache_clear()


def test_settings_test_connection_reports_sdk_missing(monkeypatch):
    from app.config import get_settings
    from app.services import llm as llm_mod

    get_settings.cache_clear()
    s = get_settings()
    s.llm_provider = "deepseek"
    s.llm_model = "deepseek-v4-pro"
    object.__setattr__(s, "deepseek_api_key", "sk-test")

    def boom(*args, **kwargs):
        return None, "未安装 openai SDK，请执行 pip install -e '.[llm]'"

    monkeypatch.setattr(llm_mod, "_complete_openai_compatible_detail", boom)
    r = client.post("/api/settings/test", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "openai SDK" in body["message"]
    get_settings.cache_clear()


def test_openai_message_text_uses_reasoning_content_fallback():
    from app.services.llm import _openai_message_text

    class Msg:
        content = ""
        reasoning_content = "OK, connection works"

    assert _openai_message_text(Msg()) == "OK, connection works"


def test_settings_test_connection_deepseek_probe_disables_thinking(monkeypatch):
    from app.config import get_settings
    from app.services import llm as llm_mod

    get_settings.cache_clear()
    s = get_settings()
    s.llm_provider = "deepseek"
    s.llm_model = "deepseek-v4-pro"
    object.__setattr__(s, "deepseek_api_key", "sk-test")

    captured: dict = {}

    def fake_detail(prompt, api_key, model, max_tokens, base_url=None, *, probe=False):
        captured["probe"] = probe
        captured["max_tokens"] = max_tokens
        return "OK", None

    monkeypatch.setattr(llm_mod, "_complete_openai_compatible_detail", fake_detail)
    r = client.post("/api/settings/test", json={})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert captured["probe"] is True
    assert captured["max_tokens"] >= 64
    get_settings.cache_clear()


def test_settings_test_connection_offline():
    r = client.post("/api/settings/test", json={})
    assert r.status_code == 200
    # No API key configured in CI → connection reports not ok, but endpoint works.
    assert "ok" in r.json()


def test_search_patents_offline_seed_corpus():
    r = client.post(
        "/api/search",
        json={"query": "防腐涂料", "source_types": ["patents"], "requirement": _REQUIREMENT, "limit_per_source": 3},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert all("title" in e for e in body["evidence"])


def test_search_literature_offline_returns_empty_gracefully():
    # arXiv / Semantic Scholar libs absent in CI → empty list, no error.
    r = client.post(
        "/api/search",
        json={"query": "epoxy corrosion", "source_types": ["literature"], "limit_per_source": 3},
    )
    assert r.status_code == 200
    assert isinstance(r.json()["evidence"], list)


def test_search_by_types_dedupes():
    req = literature.Requirement(**_REQUIREMENT)
    ev = literature.search_by_types("防腐", ["patents"], req=req, limit_per_source=5)
    ids = [e.identifier or e.title for e in ev]
    assert len(ids) == len(set(ids))


def test_search_seed_corpus_filtered_by_query():
    # Offline seed corpus is now filtered by query relevance, so different
    # queries return different, query-relevant subsets (not the full fixed list).
    req = literature.Requirement(**_REQUIREMENT)
    zinc = literature.search_by_types("zinc phosphate", ["patents"], req=req, limit_per_source=5)
    cerium = literature.search_by_types("cerium inhibitor", ["patents"], req=req, limit_per_source=5)

    zinc_ids = {e.identifier for e in zinc}
    cerium_ids = {e.identifier for e in cerium}

    # Each query surfaces its own relevant patent…
    assert "US9982145B2" in zinc_ids        # zinc phosphate primer
    assert "EP3211048A1" in cerium_ids      # cerium-based inhibitor primer
    # …and the two result sets differ (query actually drives the results).
    assert zinc_ids != cerium_ids


def test_ingest_text_file():
    content = b"Zinc phosphate is a corrosion inhibitor.\n\nEpoxy resin provides film formation."
    r = client.post("/api/ingest", files={"file": ("note.txt", content, "text/plain")})
    assert r.status_code == 200
    body = r.json()
    assert body["filename"] == "note.txt"
    assert body["total"] == 2
    assert body["evidence"][0]["source"] == "local"


def test_ingest_empty_file_returns_placeholder():
    evidence = ingestion.ingest_file("empty.txt", b"")
    assert len(evidence) == 1
    assert evidence[0].source == "local"


def test_chat_grounded_in_sources():
    sources = [
        {
            "source": "local",
            "identifier": "doc#0",
            "title": "Corrosion note",
            "snippet": "Zinc phosphate inhibits corrosion by passivating the steel surface.",
            "relevance": 1.0,
        }
    ]
    r = client.post(
        "/api/chat",
        json={"question": "防腐机理是什么？", "sources": sources, "domain": "anticorrosion_coating"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["answer"]
    assert isinstance(body["citations"], list)


def test_research_accepts_preloaded_sources():
    body = dict(_REQUIREMENT)
    body["sources"] = [
        {
            "source": "USPTO",
            "identifier": "US-TEST",
            "title": "Test patent",
            "snippet": "A waterborne epoxy primer with zinc phosphate.",
            "relevance": 0.9,
        }
    ]
    r = client.post("/api/research", json=body)
    assert r.status_code == 200
    # Pre-loaded source must surface in the evidence list.
    identifiers = [e["identifier"] for e in r.json()["evidence"]]
    assert "US-TEST" in identifiers


# ── Source availability / status tests ──────────────────────────────────────

def test_source_status_endpoint():
    r = client.get("/api/search/status")
    assert r.status_code == 200
    body = r.json()
    # All retrieval sources must be present, including the chemcrow key (v0.9).
    for src in ("patents", "literature", "internet", "notebooklm", "chemcrow"):
        assert src in body, f"Missing source: {src}"
        assert "available" in body[src]
    # chemcrow is a library-optional source; not available without intel extra.
    cc = body["chemcrow"]
    assert isinstance(cc["available"], bool)
    if not cc["available"]:
        assert cc["hint"] is not None, "chemcrow hint required when unavailable"


def test_search_response_includes_source_status():
    r = client.post(
        "/api/search",
        json={
            "query": "epoxy",
            "source_types": ["patents", "literature", "internet", "notebooklm"],
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert "source_status" in body

    # Patents always available (offline seed corpus fallback).
    assert body["source_status"]["patents"]["available"] is True
    assert body["source_status"]["patents"]["offline_fallback"] is True

    # Online sources: availability is environment-dependent (intel extra), but
    # the contract holds either way — when unavailable a setup hint is provided.
    for src in ("literature", "internet"):
        st = body["source_status"][src]
        assert isinstance(st["available"], bool)
        if not st["available"]:
            assert st["hint"] is not None

    # NotebookLM defaults to unconfigured.
    nb = body["source_status"]["notebooklm"]
    assert nb["available"] is False
    assert nb["reason"] is not None
    assert nb["hint"] is not None

    # ChemCrow key must be present (v0.9); library-optional, bool availability.
    cc = body["source_status"]["chemcrow"]
    assert isinstance(cc["available"], bool)
    if not cc["available"]:
        assert cc["hint"] is not None, "chemcrow hint required when unavailable"


def test_notebooklm_setup_status_default():
    # Default config → library_missing or not_enabled, never ready.
    status = get_setup_status()
    assert status["available"] is False
    assert status["reason"] in ("library_missing", "not_enabled", "no_notebook_id", "session_missing")
    assert status["hint"] is not None


def test_source_availability_patents_always_present():
    avail = literature.get_source_availability()
    assert avail["patents"]["available"] is True
    # Without patent_client, offline_fallback must be True.
    try:
        import patent_client  # noqa: F401
        patent_installed = True
    except Exception:
        patent_installed = False
    if not patent_installed:
        assert avail["patents"]["offline_fallback"] is True
