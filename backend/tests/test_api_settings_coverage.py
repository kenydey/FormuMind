"""Every API key / account / endpoint the backend can use has a place in Settings -> 模型与API.

Semantic Scholar's key, the Unpaywall e-mail, the MinerU / Langfuse addresses, the Langfuse keys and the Datalab token
were Settings fields that nothing in the UI could set.
"""
from __future__ import annotations

import httpx
import pytest

from app.config import Settings, get_settings
from app.services import secrets_store as ss
from app.services.runtime_secrets import effective_setting, get_runtime_secrets, setting_or_default


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("FORMUMIND_ENVIRONMENT=test\n", encoding="utf-8")
    monkeypatch.setenv("FORMUMIND_ENV_FILE", str(path))
    get_settings.cache_clear()
    ss.reload_settings()
    yield path
    get_settings.cache_clear()
    ss.reload_settings()


# ── the registry ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_every_registry_entry_is_a_settings_field_with_a_matching_env_key():
    for attr, env_key, _label, group in ss.SECRET_REGISTRY:
        assert attr in Settings.model_fields, attr
        assert env_key == f"FORMUMIND_{attr.upper()}"
        assert group in {"llm", "search", "patent", "research", "parse", "infra", "observability"}


@pytest.mark.parametrize(
    "attr, group",
    [
        ("semantic_scholar_api_key", "research"),
        ("unpaywall_mailto", "research"),
        ("mineru_base_url", "parse"),
        ("datalab_api_token", "infra"),
        ("langfuse_public_key", "observability"),
        ("langfuse_secret_key", "observability"),
        ("langfuse_host", "observability"),
    ],
)
def test_the_previously_unsettable_entries_are_in_the_settings_page(attr, group):
    assert {a: g for a, _e, _l, g in ss.SECRET_REGISTRY}[attr] == group


def test_every_credential_like_settings_field_is_either_settable_or_deliberately_not():
    """A new ``*_api_key`` / ``*_secret`` / ``*_mailto`` field must be added to the registry (or to this list, with a reason)."""
    import re

    from app.services.env_flags import FLAG_REGISTRY
    from app.services.secrets_store import _LLM_RUNTIME_ENV

    covered = {a for a, *_ in ss.SECRET_REGISTRY} | set(_LLM_RUNTIME_ENV) | {f.attr for f in FLAG_REGISTRY}
    deliberately_not = {
        "api_token": "the platform's own bearer token: Settings -> API 访问令牌 (ApiAccessPanel)",
        "redis_url": "deployment topology, changed with the compose file",
        "db_url": "deployment topology, changed with the compose file",
        "surechembl_base_url": "public endpoint, no credential is sent to it",
    }
    pattern = re.compile(r"(api_key|_secret|secret_key|password|mailto|_token$|consumer_)", re.I)
    missing = sorted(
        name for name in Settings.model_fields if pattern.search(name) and name not in covered and name not in deliberately_not
    )
    assert missing == [], f"credential-like settings with no way to set them in the UI: {missing}"


# ── saving ───────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_semantic_scholar_key_is_saved_and_used(env_file):
    ss.update_secrets({"semantic_scholar_api_key": "s2-key-0123456789-abcdef"})
    assert ss.read_env_file(env_file)["FORMUMIND_SEMANTIC_SCHOLAR_API_KEY"] == "s2-key-0123456789-abcdef"
    from app.services.scholar_helpers import semantic_scholar_headers

    assert semantic_scholar_headers()["x-api-key"] == "s2-key-0123456789-abcdef"
    item = next(i for i in ss.list_secret_status() if i["id"] == "semantic_scholar_api_key")
    assert item["set"] and item["secret"] is True and "s2-key" not in item["masked"]


def test_without_a_key_no_key_header_is_sent(env_file):
    from app.services.scholar_helpers import semantic_scholar_headers

    assert "x-api-key" not in semantic_scholar_headers()


def test_searching_semantic_scholar_sends_the_key(env_file, monkeypatch):
    from app.services import literature

    ss.update_secrets({"semantic_scholar_api_key": "s2-key-0123456789"})
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("x-api-key")
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"data": [{"title": "T", "abstract": "A", "externalIds": {"DOI": "10.1/x"}, "paperId": "p"}]})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(literature, "make_client", lambda **kw: httpx.Client(transport=transport))
    out = literature.search_semantic_scholar("zinc phosphate", limit=1)
    assert seen["key"] == "s2-key-0123456789" and "/graph/v1/paper/search" in seen["url"]
    assert [e.identifier for e in out] == ["10.1/x"]


def test_the_open_access_fallback_sends_the_key_too(env_file, monkeypatch):
    from app.services import fulltext_fetcher as ff

    ss.update_secrets({"semantic_scholar_api_key": "s2-key-0123456789"})
    seen = {}

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None, headers=None):
            seen["headers"] = headers
            return httpx.Response(404)

    import app.services.http_safe as hs

    monkeypatch.setattr(hs, "ssrf_safe_client", lambda **kw: _Client())
    from app.domain.schemas import Evidence

    ff._fetch_semanticscholar(Evidence(source="x", identifier="10.1000/abc", title="t", snippet="s", relevance=0.5), 5.0)
    assert seen["headers"]["x-api-key"] == "s2-key-0123456789"


def test_the_unpaywall_email_set_in_the_ui_is_the_one_queried(env_file, monkeypatch):
    from app.services import fulltext_fetcher as ff

    ss.update_secrets({"unpaywall_mailto": "lab@example.org"})
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["email"] = request.url.params.get("email")
        return httpx.Response(200, json={"oa_locations": []})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(ff, "make_client", lambda **kw: httpx.Client(transport=transport))
    ff._unpaywall_locations("10.1000/abc", 5.0)
    assert seen["email"] == "lab@example.org"


def test_the_datalab_token_set_in_the_ui_becomes_the_auth_header(env_file):
    from app.db.datalab_client import datalab_headers

    assert datalab_headers() == {}
    ss.update_secrets({"datalab_api_token": "dl-token-123"})
    assert datalab_headers() == {"DATALAB-API-KEY": "dl-token-123"}


def test_langfuse_keys_replace_the_cached_client(env_file, monkeypatch):
    from app.services import llm_trace

    llm_trace._client = object()
    llm_trace._client_failed = True
    ss.update_secrets({"langfuse_public_key": "pk-lf-1"})
    assert llm_trace._client is None and llm_trace._client_failed is False


def test_a_cleared_address_falls_back_to_the_default(env_file):
    s = get_settings()
    default = Settings.model_fields["mineru_base_url"].default
    ss.update_secrets({"mineru_base_url": "https://mineru.internal.example/api/v4"})
    assert setting_or_default(s, "mineru_base_url") == "https://mineru.internal.example/api/v4"
    ss.update_secrets({"mineru_base_url": ""})
    assert effective_setting(s, "mineru_base_url") in (None, "")
    assert setting_or_default(s, "mineru_base_url") == default


@pytest.mark.parametrize("attr", ["mineru_base_url", "langfuse_host", "datalab_api_url"])
def test_addresses_that_receive_credentials_are_validated(env_file, attr):
    for bad in ("http://169.254.169.254/latest", "file:///etc/passwd", "http://user:pw@host/"):
        with pytest.raises(ValueError):
            ss.update_secrets({attr: bad})
    assert get_runtime_secrets().get(attr) != "http://169.254.169.254/latest"
    ss.update_secrets({attr: "https://ok.example.org"})


def test_an_address_is_listed_in_full_and_a_key_is_masked(env_file):
    ss.update_secrets({"langfuse_host": "https://langfuse.lab.example", "unpaywall_mailto": "a@b.org", "langfuse_secret_key": "sk-lf-0123456789abcdefghij"})
    by_id = {i["id"]: i for i in ss.list_secret_status()}
    assert by_id["langfuse_host"]["masked"] == "https://langfuse.lab.example" and by_id["langfuse_host"]["secret"] is False
    assert by_id["unpaywall_mailto"]["masked"] == "a@b.org"
    assert by_id["langfuse_secret_key"]["secret"] is True and "0123456789" not in by_id["langfuse_secret_key"]["masked"]


# ── probes ───────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_semantic_scholar_probe_reports_each_outcome(env_file, monkeypatch):
    assert ss.probe_secret("semantic_scholar_api_key")["ok"] is False  # nothing configured yet
    ss.update_secrets({"semantic_scholar_api_key": "s2-key-0123456789"})
    for status, ok, fragment in ((200, True, "有效"), (403, False, "拒绝"), (429, False, "限流"), (500, False, "500")):
        transport = httpx.MockTransport(lambda request, st=status: httpx.Response(st, json={"data": []}))
        monkeypatch.setattr(ss, "make_client", lambda **kw: httpx.Client(transport=transport))
        out = ss.probe_secret("semantic_scholar_api_key")
        assert out["ok"] is ok and fragment in out["message"], (status, out)


def test_langfuse_probe_needs_both_keys(env_file):
    ss.update_secrets({"langfuse_public_key": "pk-lf-1"})
    assert ss.probe_secret("langfuse_public_key")["ok"] is False
    ss.update_secrets({"langfuse_secret_key": "sk-lf-1"})
    assert ss.probe_secret("langfuse_public_key")["ok"] is True
