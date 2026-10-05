"""Probing a Datalab that is not there must not cost two seconds on Windows (round-4).

``/health`` calls ``check_datalab_reachable`` on every request. A refused loopback connection takes ~2 s to be reported
on Windows, so a machine without Datalab — the default install — answered ``/health`` in 5-6 s (CI:
``test_health_fast_under_5s`` took 5.93 s). A process listening on this machine accepts within milliseconds, so the connect
timeout for loopback hosts is short; remote hosts keep the caller's timeout.
"""
from __future__ import annotations

import httpx
import pytest

from app.db import datalab_client


class _Fake:
    def __init__(self, **kwargs):
        _Fake.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, path):
        raise _Fake.error


@pytest.fixture()
def fake_client(monkeypatch):
    monkeypatch.setattr(datalab_client, "make_client", _Fake)
    _Fake.error = httpx.ConnectError("refused")
    return _Fake


@pytest.mark.parametrize("url", ["http://localhost:5001", "http://127.0.0.1:5001/", "http://[::1]:5001"])
def test_a_loopback_datalab_gets_a_short_connect_timeout(fake_client, url):
    ok, reason = datalab_client.check_datalab_reachable(url, timeout=2.0)
    timeout = fake_client.kwargs["timeout"]
    assert (ok, reason) == (False, "refused")
    assert timeout.connect == datalab_client.LOOPBACK_CONNECT_TIMEOUT_S
    assert timeout.read == 2.0, "only the connect phase is shortened: a busy local Datalab still gets its full reply time"


def test_a_remote_datalab_keeps_the_callers_timeout(fake_client):
    datalab_client.check_datalab_reachable("http://datalab.example.org", timeout=2.0)
    assert fake_client.kwargs["timeout"].connect == 2.0


def test_a_caller_asking_for_less_is_not_made_to_wait_longer(fake_client):
    datalab_client.check_datalab_reachable("http://localhost:5001", timeout=0.1)
    assert fake_client.kwargs["timeout"].connect == 0.1


def test_a_connect_timeout_is_explained(fake_client):
    fake_client.error = httpx.ConnectTimeout("")
    ok, reason = datalab_client.check_datalab_reachable("http://localhost:5001")
    assert ok is False and "连接超时" in reason and "localhost" in reason


def test_an_answering_datalab_is_still_reachable(monkeypatch):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    monkeypatch.setattr(
        datalab_client, "make_client", lambda **kw: httpx.Client(transport=transport, base_url=kw["base_url"])
    )
    assert datalab_client.check_datalab_reachable("http://localhost:5001") == (True, None)
