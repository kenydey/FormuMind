"""The outbound-network guard is one module (``tests/_network_guard.py``) used by the suite *and* by the API fuzzer (round-5).

The fuzzer does not run under pytest, so it never loaded the guard that ``conftest.py`` installs: its CI job sent thousands of
hostile requests to the real SureChEMBL, OpenAlex, DuckDuckGo and USPTO, and a run went red because one answered slowly.
"""
from __future__ import annotations

import errno
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import httpx
import pytest

BACKEND = Path(__file__).resolve().parents[1]
FUZZ = BACKEND.parent / "scripts" / "audit" / "api_fuzz.py"
TEST_NET = ("203.0.113.1", 80)  # RFC 5737 documentation range: never routable


def test_a_non_loopback_connect_fails_at_once_not_after_a_timeout():
    started = time.monotonic()
    with pytest.raises(OSError) as err:
        socket.create_connection(TEST_NET, timeout=30)
    assert err.value.errno == errno.ENETUNREACH
    assert time.monotonic() - started < 2


def test_loopback_still_connects():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=5):
            pass


def test_an_http_client_is_refused_by_host_even_through_a_loopback_proxy():
    with pytest.raises(httpx.ConnectError, match="outbound network is disabled"):
        httpx.get("http://example.com/", timeout=5)


def test_a_mock_transport_is_untouched():
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True})))
    assert client.get("http://example.com/").json() == {"ok": True}


def _run(code: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    import os

    env = {**os.environ, **(env_extra or {})}
    env.pop("FORMUMIND_TEST_ALLOW_NETWORK", None) if not env_extra else None
    return subprocess.run(
        [sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r)\n%s" % (str(BACKEND), textwrap.dedent(code))],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        cwd=str(BACKEND),
    )


def test_it_works_outside_pytest_which_is_how_the_fuzzer_uses_it():
    """No conftest, no marker fixture: ``install()`` alone denies the network."""
    done = _run(
        """
        import errno, socket
        from tests import _network_guard
        _network_guard.install()
        try:
            socket.create_connection(("203.0.113.1", 80), timeout=30)
        except OSError as exc:
            print("refused", exc.errno == errno.ENETUNREACH)
        """
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["refused", "True"]


def test_the_environment_switch_opens_it_for_a_developer_who_wants_the_real_services():
    done = _run(
        """
        from tests import _network_guard
        _network_guard.install()
        print(_network_guard.host_refusal("example.com"))
        """,
        {"FORMUMIND_TEST_ALLOW_NETWORK": "1"},
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "None"


def test_install_twice_only_replaces_the_predicate():
    done = _run(
        """
        import socket
        from tests import _network_guard
        _network_guard.install()
        first = socket.socket.connect
        _network_guard.install(lambda: True)  # conftest's marker fixture flips this per test
        print(socket.socket.connect is first, _network_guard.host_refusal("example.com"))
        """
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["True", "None"]


def test_the_fuzzer_installs_the_guard_before_it_imports_the_application():
    source = FUZZ.read_text(encoding="utf-8")
    install = source.index("_network_guard.install()")
    assert install < source.index("import app.api._dispatch"), "an import-time client would be created unguarded"
    assert install < source.index("from app.main import app")
