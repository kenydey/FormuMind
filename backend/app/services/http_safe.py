"""Outbound HTTP: one shared TLS context, and an SSRF-safe client for URLs we did not choose.

**Cost.** ``httpx.Client()`` loads the CA bundle into a fresh ``ssl.SSLContext`` every time it is
built — ≈ 48 ms against ≈ 1 ms with a shared context — and the ingestion and literature paths build
one client per request (38 call sites; a 20-document full-text batch made on the order of a hundred
of them). :func:`make_client` / :func:`make_async_client` are drop-in replacements that hand every
client the same context.

**SSRF.** ``is_safe_url`` resolves the host and judges the answers, but httpx then resolved the same
name *again* to connect. A DNS server that answers with a public address for the check and with
``127.0.0.1`` for the connection (a TTL-0 "rebinding" answer) passed the check and was fetched.
:func:`ssrf_safe_client` closes that window: :class:`PinnedTransport` resolves the host once per
request, refuses unless *every* answer is public, and connects to an address it has judged — the URL
carries the IP literal, while the ``Host`` header and the TLS server name keep the original host so
virtual hosting and certificate verification behave as before. Redirects pass through the same
transport, so each hop is re-resolved and re-judged (the callers keep their own per-hop
``is_safe_url`` check for the friendlier reason codes).

Two deliberate limits:

* A request that an environment proxy (``HTTPS_PROXY`` / ``NO_PROXY``) handles is passed to that
  proxy unpinned: the proxy resolves the name, not this process, and an egress proxy is where that
  policy has to live. (httpx also ignores env proxies once a ``transport=`` is given, which would
  have broken every proxied deployment.)
* The pinned transport keeps no idle connections. The pool keys a connection by *address*, so a
  kept-alive connection could otherwise carry a later request for a different host that merely
  shares the IP — over a certificate that was never checked for that name.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import ssl
import threading
import urllib.request
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# A multi-homed host is tried address by address on connection failure, like ``create_connection``
# does, but never for more than this many.
_MAX_ADDRESSES_TRIED = 4

_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")  # RFC 6052: the low 32 bits are an IPv4 address


# ── shared TLS context ───────────────────────────────────────────────────────

_ctx_lock = threading.Lock()
_ctx_cache: dict[tuple[str, str], ssl.SSLContext] = {}


def shared_ssl_context() -> ssl.SSLContext:
    """The process-wide client TLS context (CA bundle loaded once).

    Keyed by the two environment variables httpx itself honours, so a changed
    ``SSL_CERT_FILE`` / ``SSL_CERT_DIR`` still takes effect.
    """
    key = (os.environ.get("SSL_CERT_FILE", ""), os.environ.get("SSL_CERT_DIR", ""))
    ctx = _ctx_cache.get(key)
    if ctx is None:
        with _ctx_lock:
            ctx = _ctx_cache.get(key)
            if ctx is None:
                ctx = _ctx_cache[key] = httpx.create_ssl_context()
    return ctx


def make_client(**kwargs: Any) -> httpx.Client:
    """``httpx.Client(**kwargs)`` that reuses the shared TLS context unless told otherwise."""
    kwargs.setdefault("verify", shared_ssl_context())
    return httpx.Client(**kwargs)


def make_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """``httpx.AsyncClient(**kwargs)`` that reuses the shared TLS context unless told otherwise."""
    kwargs.setdefault("verify", shared_ssl_context())
    return httpx.AsyncClient(**kwargs)


# ── address policy ───────────────────────────────────────────────────────────


class UnsafeAddressError(httpx.TransportError):
    """The host is, or resolves to, an address a server-side fetch must never reach."""


def normalize_host(host: str) -> str:
    return host.strip().lower().rstrip(".")


def is_blocked_ip(addr: IPAddress) -> bool:
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:  # ::ffff:a.b.c.d is judged as a.b.c.d
        return is_blocked_ip(mapped)
    if isinstance(addr, ipaddress.IPv6Address) and addr in _NAT64:
        # 64:ff9b::7f00:1 is "globally routable" to the stdlib, but a NAT64 gateway turns it
        # into 127.0.0.1.
        return is_blocked_ip(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF))
    return (
        # Anything not globally routable: also covers the CGNAT block
        # 100.64.0.0/10 (carrier-grade NAT; Alibaba's metadata service lives at
        # 100.100.100.200), which ``is_private`` does not flag.
        not addr.is_global
        or addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def is_blocked_hostname(host: str) -> bool:
    host = normalize_host(host)
    if host in ("localhost", "localhost.localdomain", "0.0.0.0"):
        return True
    return host.endswith(".localhost") or host.endswith(".local")


_LEGACY_IPV4 = re.compile(r"^(?:0[xX][0-9a-fA-F]+|[0-9]+)(?:\.(?:0[xX][0-9a-fA-F]+|[0-9]+)){0,3}$")


def parse_legacy_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """The address ``inet_aton`` reads from *host*: ``127.1``, ``2130706433``, ``0177.0.0.1``, ``0x7f.1`` …

    The stdlib's ``ipaddress`` only accepts the dotted quad, and whether ``getaddrinfo`` accepts the other
    spellings is up to the operating system: glibc resolves ``127.1`` to 127.0.0.1, Windows' resolver does not —
    it fails, the pre-flight then saw "no addresses to refuse" and let the URL through to a connection attempt.
    A host that is an IPv4 address in disguise is judged here, the same way everywhere.
    """
    if not _LEGACY_IPV4.match(host):
        return None
    try:
        numbers = [int(part, 16) if part[:2] in ("0x", "0X") else int(part, 8) if len(part) > 1 and part[0] == "0" else int(part)
                   for part in host.split(".")]
    except ValueError:  # "08", "09": not valid octal — inet_aton rejects them too
        return None
    *head, last = numbers
    if any(n > 255 for n in head) or last >= 256 ** (4 - len(head)):
        return None
    value = 0
    for n in head:
        value = (value << 8) | n
    return ipaddress.IPv4Address((value << (8 * (4 - len(head)))) | last)


def resolve_addresses(host: str) -> list[IPAddress]:
    """Every address *host* resolves to, IPv4 first; a literal resolves to itself.

    An unresolvable host yields an empty list (the connection attempt reports the DNS failure).
    """
    host = normalize_host(host)
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    legacy = parse_legacy_ipv4(host)
    if legacy is not None:
        return [legacy]
    found: list[IPAddress] = []
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            infos = socket.getaddrinfo(host, None, family, socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError):
            continue
        for info in infos:
            addr = ipaddress.ip_address(info[4][0])
            if addr not in found:
                found.append(addr)
    return found


def check_public_addresses(host: str) -> list[IPAddress]:
    """Resolve once and judge: the addresses to connect to, or :class:`UnsafeAddressError`.

    All answers must be public — a name with one public and one private record is refused rather
    than trusted to pick the right one.
    """
    if not host:
        raise UnsafeAddressError("request has no host")
    if is_blocked_hostname(host):
        raise UnsafeAddressError(f"{host} is not a public host")
    addresses = resolve_addresses(host)
    for addr in addresses:
        if is_blocked_ip(addr):
            raise UnsafeAddressError(f"{host} resolves to a non-public address ({addr})")
    return addresses


def is_safe_url(url: str) -> bool:
    """True for an http(s) URL whose host is public (the cheap pre-flight; the transport re-checks)."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    try:
        check_public_addresses(host)
    except UnsafeAddressError:
        return False
    return True


# ── environment proxies ──────────────────────────────────────────────────────


def env_proxy_for(url: httpx.URL) -> str | None:
    """The proxy ``HTTP(S)_PROXY`` / ``ALL_PROXY`` selects for *url*, honouring ``NO_PROXY``."""
    proxies = urllib.request.getproxies()
    proxy = proxies.get(url.scheme) or proxies.get("all")
    if not proxy:
        return None
    try:
        if urllib.request.proxy_bypass(url.host):
            return None
    except Exception:  # noqa: BLE001 - a malformed NO_PROXY must not take the request down
        pass
    return proxy


# ── pinned transport ─────────────────────────────────────────────────────────


def _pinned_request(request: httpx.Request, host: str, addr: IPAddress) -> httpx.Request:
    """*request* re-aimed at *addr*, still addressed to *host* as far as the server can tell."""
    headers = request.headers
    if "host" not in headers:  # httpx adds it at build time; a hand-made request may lack it
        headers = httpx.Headers(headers)
        headers["Host"] = request.url.netloc.decode("ascii")
    extensions = dict(request.extensions)  # keeps the per-request timeouts
    if request.url.scheme == "https" and not _is_literal(host):
        extensions["sni_hostname"] = host  # also the name the certificate is verified against
    return httpx.Request(
        request.method,
        request.url.copy_with(host=str(addr)),
        headers=headers,
        stream=request.stream,
        extensions=extensions,
    )


def _is_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


class PinnedTransport(httpx.BaseTransport):
    """Connect only to addresses this transport resolved and judged itself (see module docs)."""

    def __init__(
        self,
        *,
        verify: ssl.SSLContext | bool | str | None = None,
        direct: httpx.BaseTransport | None = None,
        via_proxy: Callable[[str], httpx.BaseTransport] | None = None,
    ) -> None:
        self._verify = shared_ssl_context() if verify is None else verify
        self._direct = direct or httpx.HTTPTransport(
            verify=self._verify, limits=httpx.Limits(max_keepalive_connections=0)
        )
        self._via_proxy = via_proxy or self._default_via_proxy
        self._proxied: dict[str, httpx.BaseTransport] = {}
        self._lock = threading.Lock()

    def _default_via_proxy(self, proxy: str) -> httpx.BaseTransport:
        return httpx.HTTPTransport(verify=self._verify, proxy=proxy)

    def _proxy_transport(self, proxy: str) -> httpx.BaseTransport:
        with self._lock:
            transport = self._proxied.get(proxy)
            if transport is None:
                transport = self._proxied[proxy] = self._via_proxy(proxy)
            return transport

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        proxy = env_proxy_for(request.url)
        if proxy:
            return self._proxy_transport(proxy).handle_request(request)

        host = request.url.raw_host.decode("ascii") if request.url.raw_host else ""
        try:
            addresses = check_public_addresses(host)
        except UnsafeAddressError as exc:
            raise UnsafeAddressError(str(exc), request=request) from None
        if not addresses:
            raise httpx.ConnectError(f"cannot resolve {host}", request=request)

        error: Exception | None = None
        for addr in addresses[:_MAX_ADDRESSES_TRIED]:
            try:
                return self._direct.handle_request(_pinned_request(request, host, addr))
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                error = exc  # nothing was sent yet: safe to try the next address
        assert error is not None
        raise error

    def close(self) -> None:
        self._direct.close()
        for transport in self._proxied.values():
            transport.close()


def ssrf_safe_client(**kwargs: Any) -> httpx.Client:
    """A :func:`make_client` whose connections only ever go to public addresses."""
    if "transport" in kwargs or "mounts" in kwargs:
        raise TypeError("ssrf_safe_client installs its own transport")
    verify = kwargs.setdefault("verify", shared_ssl_context())
    return make_client(transport=PinnedTransport(verify=verify), **kwargs)
