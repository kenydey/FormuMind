"""The source-status payload must describe what the search code can actually do.

* Semantic Scholar is queried over plain HTTP, but the probe asked for the ``semanticscholar`` SDK - which no code
  imports and no extra installs - so ``literature`` read "library_missing" (and the UI showed a banner that nothing the
  user could install would clear) whenever OpenAlex happened to be switched off.
* EPO credentials flipped the patent status to "available" although both the EPO and the USPTO search go through the
  ``patent_client`` SDK: with the keys set and the SDK absent, the status said yes and the search returned nothing.
* the hint for USPTO/EPO said ``pip install -e '.[intel]'``, which no longer (and, with the pins held, never could)
  install ``patent-client``.
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


def test_epo_credentials_without_the_sdk_are_not_availability(monkeypatch, probes):
    settings = get_settings()
    monkeypatch.setattr(settings, "epo_consumer_key", "key", raising=False)
    monkeypatch.setattr(settings, "epo_consumer_secret", "secret", raising=False)
    status = literature.get_source_availability()
    assert status["epo"]["available"] is False
    assert status["epo"]["reason"] == "library_missing"
    assert ".[patents]" in status["epo"]["hint"]
    assert status["patents"]["reason"] == "offline_seed"


def test_without_credentials_the_epo_hint_is_still_about_the_keys(monkeypatch, probes):
    settings = get_settings()
    monkeypatch.setattr(settings, "epo_consumer_key", None, raising=False)
    monkeypatch.setattr(settings, "epo_consumer_secret", None, raising=False)
    probes.add("patent_client")
    epo = literature.get_source_availability()["epo"]
    assert epo["available"] is False and epo["reason"] == "key_missing"
    assert "EPO_CONSUMER" in epo["hint"]


def test_the_sdk_and_the_keys_together_make_both_available(monkeypatch, probes):
    settings = get_settings()
    monkeypatch.setattr(settings, "epo_consumer_key", "key", raising=False)
    monkeypatch.setattr(settings, "epo_consumer_secret", "secret", raising=False)
    probes.add("patent_client")
    status = literature.get_source_availability()
    assert status["epo"]["available"] is True and status["epo"]["reason"] is None
    assert status["patents"]["reason"] is None and status["patents"]["hint"] is None


def test_the_patent_hint_names_the_extra_that_installs_the_sdk_and_what_it_costs(probes):
    patents = literature.get_source_availability()["patents"]
    assert patents["available"] is True and patents["offline_fallback"] is True  # the seed corpus still answers
    assert ".[patents]" in patents["hint"]
    assert ".[intel]" not in patents["hint"]
    assert "httpx" in patents["hint"] and "pypdf" in patents["hint"]  # the price is stated where the install is offered


def test_no_hint_sends_the_user_to_install_a_retired_integration(probes):
    """ChemCrow was removed (de-ChemCrow 2026-09): no extra installs it and no code imports it, yet the status said
    `pip install -e '.[intel]'` would enable it."""
    status = literature.get_source_availability()
    assert status["literature"]["hint"] is None
    chemcrow_hint = status["chemcrow"]["hint"]
    assert chemcrow_hint is not None, "the key is kept for API compatibility and must still explain itself"
    assert "pip install" not in chemcrow_hint and "退役" in chemcrow_hint
