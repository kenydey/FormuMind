"""Cold start on a rented, scale-to-zero vision endpoint.

A HuggingFace Inference Endpoint with scale-to-zero enabled answers **503**
while it boots a replica. Everything here exists so that minutes of waiting are
attributed correctly instead of being reported as a broken configuration — the
failure mode that sends you hunting for a bad API key when the endpoint is fine
and simply was not running.
"""
from __future__ import annotations

import sys
import types

import pytest

from app.config import get_settings
from app.services import vision_extract
from app.services.llm_roles import is_warming
from app.services.runtime_secrets import get_runtime_secrets

_ENV_KEYS = (
    "FORMUMIND_LLM_PROVIDER",
    "FORMUMIND_LLM_BASE_URL",
    "FORMUMIND_VISION_PROVIDER",
    "FORMUMIND_VISION_MODEL",
    "FORMUMIND_VISION_BASE_URL",
    "FORMUMIND_VISION_API_KEY",
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("FORMUMIND_ENV_FILE", str(env_file))
    rs = get_runtime_secrets()
    rs.clear()
    get_settings.cache_clear()
    yield
    rs.clear()
    get_settings.cache_clear()


def _custom_endpoint():
    rs = get_runtime_secrets()
    rs.set("vision_provider", "custom")
    rs.set("vision_model", "tgi")
    rs.set("vision_api_key", "hf_token")
    rs.set("vision_base_url", "https://vlm.endpoints.huggingface.cloud/v1")


class _Warming(Exception):
    """Shaped like an SDK error carrying an HTTP response."""

    def __init__(self):
        super().__init__("Service Unavailable")
        self.response = types.SimpleNamespace(status_code=503)


def _fake_openai(monkeypatch, *, raises: Exception | None = None):
    """Inject a fake `openai` module; records calls, optionally raises."""
    mod = types.ModuleType("openai")
    calls: list[dict] = []

    class OpenAI:
        def __init__(self, **kwargs):
            calls.append(kwargs)

            def create(**kw):
                if raises is not None:
                    raise raises
                msg = types.SimpleNamespace(content='{"kind":"other","markdown":"x"}')
                return types.SimpleNamespace(
                    choices=[types.SimpleNamespace(message=msg)]
                )

            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=create)
            )

    mod.OpenAI = OpenAI
    monkeypatch.setitem(sys.modules, "openai", mod)
    return calls


# ── classification ───────────────────────────────────────────────────────────


def test_503_is_classified_as_warming():
    assert is_warming(_Warming()) is True


def test_other_statuses_are_not_warming():
    for status in (400, 401, 403, 404, 429, 500, 502, 504):
        exc = Exception("nope")
        exc.response = types.SimpleNamespace(status_code=status)
        assert is_warming(exc) is False, status


def test_a_plain_exception_is_not_warming():
    assert is_warming(ValueError("boom")) is False


def test_warming_produces_an_actionable_hint(monkeypatch):
    """A 503 must not read as a broken key — that is the wrong place to look."""
    _custom_endpoint()
    _fake_openai(monkeypatch, raises=_Warming())

    extraction, err = vision_extract.extract_image(b"\x89PNG fake", "fig.png")
    assert extraction is None
    assert "冷启动" in err
    assert "503" in err


def test_a_real_error_keeps_the_provider_message(monkeypatch):
    _custom_endpoint()
    exc = Exception("Invalid API key supplied")
    exc.response = types.SimpleNamespace(status_code=401)
    _fake_openai(monkeypatch, raises=exc)

    _, err = vision_extract.extract_image(b"\x89PNG fake", "fig.png")
    assert "Invalid API key" in err
    assert "冷启动" not in err


# ── prewarm ──────────────────────────────────────────────────────────────────


def test_prewarm_wakes_a_custom_endpoint(monkeypatch):
    _custom_endpoint()
    calls = _fake_openai(monkeypatch)

    ok, _elapsed, hint = vision_extract.prewarm()
    assert ok is True and hint == ""
    assert len(calls) == 1
    # It must carry the cold-start hold, or the wake-up call itself 503s.
    assert "X-Scale-Up-Timeout" in calls[0]["default_headers"]
    assert calls[0]["base_url"] == "https://vlm.endpoints.huggingface.cloud/v1"


def test_prewarm_is_a_noop_for_hosted_providers(monkeypatch):
    """Nobody should pay an extra request to "wake" an always-on API."""
    rs = get_runtime_secrets()
    rs.set("llm_provider", "openai")
    rs.set("openai_api_key", "sk-test")
    calls = _fake_openai(monkeypatch)

    ok, elapsed, hint = vision_extract.prewarm()
    assert (ok, elapsed, hint) == (True, 0.0, "")
    assert calls == []


def test_prewarm_reports_a_still_cold_endpoint(monkeypatch):
    _custom_endpoint()
    _fake_openai(monkeypatch, raises=_Warming())

    ok, _elapsed, hint = vision_extract.prewarm()
    assert ok is False
    assert "冷启动" in hint


def test_prewarm_declines_when_vision_is_unconfigured(monkeypatch):
    ok, elapsed, hint = vision_extract.prewarm()
    assert ok is False and elapsed == 0.0
    assert hint  # says why


def test_failed_prewarm_does_not_stop_the_pages(monkeypatch):
    """Best-effort by contract: the per-page calls may still succeed, and
    skipping every figure because one wake-up call failed would lose content for
    no reason."""
    from app.services import hybrid_parse

    seen: list[int] = []
    warmed: list[bool] = []

    def _cold():
        warmed.append(True)
        return False, 1.0, "still cold"

    monkeypatch.setattr(vision_extract, "prewarm", _cold)
    monkeypatch.setattr(
        hybrid_parse, "_escalate_page",
        lambda content, page: seen.append(page.page_no) or "escalated",
    )

    page = types.SimpleNamespace(page_no=1, markdown="local", tables=1, images=0)
    monkeypatch.setattr(hybrid_parse.pdf_local, "extract_pages", lambda c: [page])
    monkeypatch.setattr(hybrid_parse.pdf_local, "looks_scanned", lambda pages: False)
    monkeypatch.setattr(hybrid_parse.pdf_local, "assemble", lambda parts: parts[0][1])
    monkeypatch.setattr(hybrid_parse, "_needs_escalation", lambda p: True)
    monkeypatch.setattr(hybrid_parse.mineru_cloud, "mineru_available", lambda: (True, ""))

    out = hybrid_parse.parse(b"%PDF-1.4 fake")
    assert warmed == [True], "prewarm must actually run before the loop"
    assert seen == [1], "a failed prewarm must not skip the escalation"
    assert out == "escalated"


def test_prewarm_runs_once_for_a_whole_document(monkeypatch):
    """The escalation loop is sequential, so one wake-up covers every page —
    waking per page would pay the boot repeatedly for nothing."""
    from app.services import hybrid_parse

    warmed: list[bool] = []
    monkeypatch.setattr(
        vision_extract, "prewarm", lambda: warmed.append(True) or (True, 0.5, "")
    )
    monkeypatch.setattr(hybrid_parse, "_escalate_page", lambda content, page: "escalated")

    pages = [
        types.SimpleNamespace(page_no=n, markdown="local", tables=1, images=0)
        for n in (1, 2, 3)
    ]
    monkeypatch.setattr(hybrid_parse.pdf_local, "extract_pages", lambda c: pages)
    monkeypatch.setattr(hybrid_parse.pdf_local, "looks_scanned", lambda p: False)
    monkeypatch.setattr(hybrid_parse.pdf_local, "assemble", lambda parts: "|".join(p[1] for p in parts))
    monkeypatch.setattr(hybrid_parse, "_needs_escalation", lambda p: True)
    monkeypatch.setattr(hybrid_parse.mineru_cloud, "mineru_available", lambda: (True, ""))

    hybrid_parse.parse(b"%PDF-1.4 fake")
    assert warmed == [True], "one prewarm per document, not per page"


def test_no_prewarm_when_no_page_needs_escalation(monkeypatch):
    """No figures to read means no reason to spin up a GPU."""
    from app.services import hybrid_parse

    warmed: list[bool] = []
    monkeypatch.setattr(
        vision_extract, "prewarm", lambda: warmed.append(True) or (True, 0.0, "")
    )
    page = types.SimpleNamespace(page_no=1, markdown="local", tables=0, images=0)
    monkeypatch.setattr(hybrid_parse.pdf_local, "extract_pages", lambda c: [page])
    monkeypatch.setattr(hybrid_parse.pdf_local, "looks_scanned", lambda p: False)
    monkeypatch.setattr(hybrid_parse.pdf_local, "assemble", lambda parts: parts[0][1])
    monkeypatch.setattr(hybrid_parse, "_needs_escalation", lambda p: False)
    monkeypatch.setattr(hybrid_parse.mineru_cloud, "mineru_available", lambda: (True, ""))

    hybrid_parse.parse(b"%PDF-1.4 fake")
    assert warmed == []
