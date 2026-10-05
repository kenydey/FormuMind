"""An unreachable PubChem must cost one timeout, not one per ingredient (round-4 profiling finding).

The first recommendation after a start spent 12-25 s in ``chemical_lookup`` → ``chemtools._pubchem_get`` — one cold
name lookup after another, each waiting out its own timeout, inside the request — on a machine with no route to PubChem
(an air-gapped lab, a firewall that drops packets), which is the offline mode the product promises. After a transport-level
failure the lookups are now skipped for a while (they all degrade to "unknown" and are negatively cached), and the
*connect* phase — which a reachable service completes in milliseconds — gets a few seconds, not the full read timeout.
"""
from __future__ import annotations

import httpx
import pytest

from app.services import chemtools


class _Client:
    calls = 0
    behaviour: object = httpx.ConnectTimeout("")
    timeouts: list = []

    def __init__(self, **kwargs):
        _Client.timeouts.append(kwargs.get("timeout"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url):
        _Client.calls += 1
        if isinstance(_Client.behaviour, Exception):
            raise _Client.behaviour
        return _Client.behaviour


@pytest.fixture(autouse=True)
def client(monkeypatch):
    _Client.calls, _Client.timeouts, _Client.behaviour = 0, [], httpx.ConnectTimeout("")
    monkeypatch.setattr(chemtools, "make_client", _Client)
    chemtools.clear_cache()
    yield _Client
    chemtools.clear_cache()


def test_a_transport_failure_skips_the_following_lookups(client):
    assert chemtools._pubchem_get("/pug/compound/name/epoxy/JSON") is None
    assert client.calls == 1
    for name in ("hardener", "zinc phosphate", "xylene"):
        assert chemtools._pubchem_get(f"/pug/compound/name/{name}/JSON") is None
    assert client.calls == 1, "a lookup was attempted while PubChem was known to be unreachable"


def test_the_lookups_resume_after_the_window(client):
    chemtools._pubchem_get("/a")
    chemtools._pubchem_down_until = 0.0  # the window has passed
    chemtools._pubchem_get("/b")
    assert client.calls == 2


@pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.ReadTimeout(""), httpx.ConnectTimeout("")])
def test_every_transport_error_opens_the_window(client, error):
    client.behaviour = error
    chemtools._pubchem_get("/a")
    chemtools._pubchem_get("/b")
    assert client.calls == 1


def test_an_http_error_answer_does_not_open_the_window(client):
    client.behaviour = httpx.Response(404, json={})
    assert chemtools._pubchem_get("/missing") is None
    assert chemtools._pubchem_get("/missing-too") is None
    assert client.calls == 2, "PubChem answered; a 404 for one name says nothing about the next"


def test_a_success_is_returned(client):
    client.behaviour = httpx.Response(200, json={"ok": 1})
    assert chemtools._pubchem_get("/fine") == {"ok": 1}


def test_the_connect_phase_gets_a_few_seconds_the_read_phase_keeps_its_setting(client):
    chemtools._pubchem_get("/a")
    timeout = client.timeouts[0]
    assert timeout.connect == chemtools._PUBCHEM_CONNECT_TIMEOUT_S
    assert timeout.read == pytest.approx(float(chemtools.get_settings().chemtools_timeout_s))


def test_clearing_the_cache_also_closes_the_window(client):
    chemtools._pubchem_get("/a")
    assert chemtools._pubchem_down_until > 0
    chemtools.clear_cache()
    assert chemtools._pubchem_down_until == 0.0
