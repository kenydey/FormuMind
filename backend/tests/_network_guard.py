"""Outbound-network denial, shared by the pytest suite (``conftest.py``) and the API fuzzer (``scripts/audit/api_fuzz.py``).

The suite used to reach the public internet: a tracer run counted ~330 real requests to PubChem (285, 117 from
test_research_endpoint alone), OpenAlex, Semantic Scholar, SureChEMBL and DuckDuckGo, and a stream test sent a fake key to
api.deepseek.com. Results then depended on third-party uptime and rate limits (a 429 from OpenAlex is just one of the ways
that bites), and daemon threads left behind by one test kept calling out during later ones.

The fuzzer had the same problem and nobody noticed, because it does not run under pytest and so never loaded the guard: its
CI job sent thousands of hostile requests (``'; DROP TABLE experiments;--``, 20 KB strings, NUL bytes) to the real SureChEMBL,
OpenAlex, DuckDuckGo and USPTO, and a run was red when one of them answered slowly - ``/api/wiki/literature/enrich-oa`` took
longer than the 20 s budget although the request was the valid base request, not a mutated one.

Every non-loopback connect now fails fast (ENETUNREACH) - exactly the "offline" degrade the application code already handles -
unless the caller's ``is_allowed`` says otherwise (the suite: a test marked ``@pytest.mark.network``, or
``FORMUMIND_TEST_ALLOW_NETWORK=1``).
"""
from __future__ import annotations

import errno
import ipaddress
import os
import socket
from collections.abc import Callable
from urllib.parse import urlparse

ENV_ALLOW = "FORMUMIND_TEST_ALLOW_NETWORK"

_is_allowed: Callable[[], bool] = lambda: False  # noqa: E731 - replaced by install()
_installed = False
_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex


def is_local_address(address) -> bool:
    if isinstance(address, (str, bytes)):  # AF_UNIX path
        return True
    host = address[0]
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    host = str(host).split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost"
    return ip.is_loopback or ip.is_unspecified


def _allow_all() -> bool:
    return os.environ.get(ENV_ALLOW, "").strip().lower() in {"1", "true", "yes"}


def blocked(address) -> bool:
    return not (_is_allowed() or _allow_all() or is_local_address(address))


def _guarded_connect(self, address):
    if blocked(address):
        raise OSError(
            errno.ENETUNREACH,
            f"outbound network is disabled in tests ({address[0]!r}); mock it, or mark the test with @pytest.mark.network",
        )
    return _REAL_CONNECT(self, address)


def _guarded_connect_ex(self, address):
    if blocked(address):
        return errno.ENETUNREACH
    return _REAL_CONNECT_EX(self, address)


def host_refusal(host: str | None) -> str | None:
    """The message for a request to ``host`` that must not leave the machine, else None."""
    if _is_allowed() or _allow_all() or is_local_address((host or "", 0)):
        return None
    return f"outbound network is disabled in tests ({host!r}); mock it, or mark the test with @pytest.mark.network"


def _install_http_client_guards() -> None:
    """The socket layer cannot see through an HTTP(S) proxy (a loopback proxy is "local"), so the HTTP clients are also gated
    by the *request host*. Transport-level, so ``httpx.MockTransport``-based tests are unaffected."""
    try:
        import httpx

        real_handle = httpx.HTTPTransport.handle_request

        def handle_request(self, request):
            msg = host_refusal(request.url.host)
            if msg:
                raise httpx.ConnectError(msg, request=request)
            return real_handle(self, request)

        httpx.HTTPTransport.handle_request = handle_request  # type: ignore[method-assign]

        real_ahandle = httpx.AsyncHTTPTransport.handle_async_request

        async def handle_async_request(self, request):
            msg = host_refusal(request.url.host)
            if msg:
                raise httpx.ConnectError(msg, request=request)
            return await real_ahandle(self, request)

        httpx.AsyncHTTPTransport.handle_async_request = handle_async_request  # type: ignore[method-assign]
    except ImportError:
        pass
    try:
        import requests

        real_send = requests.adapters.HTTPAdapter.send

        def send(self, request, *args, **kwargs):
            msg = host_refusal(urlparse(request.url).hostname)
            if msg:
                raise requests.exceptions.ConnectionError(msg, request=request)
            return real_send(self, request, *args, **kwargs)

        requests.adapters.HTTPAdapter.send = send  # type: ignore[method-assign]
    except ImportError:
        pass
    try:
        import urllib.error
        import urllib.request

        real_open = urllib.request.OpenerDirector.open

        def opener_open(self, fullurl, *args, **kwargs):
            url = fullurl if isinstance(fullurl, str) else getattr(fullurl, "full_url", "")
            msg = host_refusal(urlparse(url).hostname)
            if msg:
                raise urllib.error.URLError(msg)
            return real_open(self, fullurl, *args, **kwargs)

        urllib.request.OpenerDirector.open = opener_open  # type: ignore[method-assign]
    except ImportError:
        pass


def install(is_allowed: Callable[[], bool] | None = None) -> None:
    """Deny non-loopback connections in this process. Idempotent; a later call only replaces ``is_allowed``."""
    global _is_allowed, _installed
    if is_allowed is not None:
        _is_allowed = is_allowed
    if _installed:
        return
    _installed = True
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]
    _install_http_client_guards()
