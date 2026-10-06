"""The source-status payload must describe what the search code can actually do.

* Semantic Scholar is queried over plain HTTP, but the probe asked for the ``semanticscholar`` SDK - which no code
  imports and no extra installs - so ``literature`` read "library_missing" (and the UI showed a banner that nothing the
  user could install would clear) whenever OpenAlex happened to be switched off.
* EPO credentials flipped the patent status to "available" although the search went through the ``patent_client`` SDK
  that was not installed. (The SDK is gone - the search is EPO OPS over httpx, so the credentials *are* the
  availability; that half is in ``test_epo_patent_search.py``.)
* the hint for USPTO/EPO said ``pip install -e '.[intel]'``, which could never install it.
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.services import literature


@pytest.fixture()
def probes(monkeypatch):
    """Control which optional modules the status code finds importable."""
    present: set[str] = set()
    monkeypatch.setattr(literature, "optional_import", lambda module: module in present)
    return present


def test_literature_search_needs_nothing_installed(monkeypatch, probes):
    monkeypatch.setattr(get_settings(), "openalex_enabled", False, raising=False)
    status = literature.get_source_availability()["literature"]
    assert status["available"] is True
    assert status["reason"] is None


def test_patent_availability_does_not_depend_on_any_installed_module(monkeypatch, probes):
    """Nothing to import any more: the same answer with no optional module importable at all."""
    settings = get_settings()
    monkeypatch.setattr(settings, "epo_consumer_key", "key", raising=False)
    monkeypatch.setattr(settings, "epo_consumer_secret", "secret", raising=False)
    assert not probes
    status = literature.get_source_availability()
    assert status["epo"]["available"] is True and status["patents"]["reason"] is None


def test_no_hint_sends_the_user_to_install_a_retired_integration(probes):
    """ChemCrow was removed (de-ChemCrow 2026-09): no extra installs it and no code imports it, yet the status said
    `pip install -e '.[intel]'` would enable it."""
    status = literature.get_source_availability()
    assert status["literature"]["hint"] is None
    chemcrow_hint = status["chemcrow"]["hint"]
    assert chemcrow_hint is not None, "the key is kept for API compatibility and must still explain itself"
    assert "pip install" not in chemcrow_hint and "退役" in chemcrow_hint
