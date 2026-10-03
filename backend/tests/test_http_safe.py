"""Outbound HTTP: the shared TLS context and the pinned-IP SSRF transport (round-3 P1-5 / P2-12).

``is_safe_url`` resolved a host, judged the answers, and then let httpx resolve the *same name
again* to connect — so a DNS answer that changed in between (rebinding) sailed through. The
transport now resolves once per request and connects to the address it judged. These tests play
the attack timeline with a scripted resolver: public for the pre-flight, loopback afterwards.
"""
from __future__ import annotations

import ast
import datetime
import errno
import http.server
import ipaddress
import os
import socket
import ssl
import threading
from pathlib import Path

import httpx
import pytest

from app.services import fulltext_fetcher as ft
from app.services import http_safe, ingestion
from app.services.http_safe import (
    PinnedTransport,
    UnsafeAddressError,
    is_blocked_ip,
    is_safe_url,
    make_client,
    shared_ssl_context,
    ssrf_safe_client,
)
from app.services.pdf_downloader import fetch_patent_landing, fetch_pdf_ex

PUBLIC = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


@pytest.fixture(autouse=True)
def _no_env_proxy(monkeypatch):
    """A proxy in the environment (this sandbox has one) legitimately bypasses pinning."""
    for key in list(os.environ):
        if key.lower().endswith("_proxy"):
            monkeypatch.delenv(key, raising=False)


class FakeDns:
    """``socket.getaddrinfo`` with a script per host: one answer per *resolution pass*.

    A pass is the A lookup plus the AAAA lookup that follows it, so ``set("h", [public],
    [loopback])`` answers the pre-flight with the public address and every later pass with
    loopback — the rebinding timeline.
    """

    def __init__(self) -> None:
        self.script: dict[str, list[list[str]]] = {}
        self.passes: dict[str, int] = {}
        self.lookups: list[tuple[str, int]] = []

    def set(self, host: str, *answers: list[str]) -> None:
        self.script[host] = [list(a) for a in answers]
        self.passes[host] = 0

    @property
    def resolutions(self) -> int:
        return sum(1 for _host, family in self.lookups if family == socket.AF_INET)

    def __call__(self, host, port=None, family=0, type=0, proto=0, flags=0):
        self.lookups.append((host, family))
        answers = self.script.get(host)
        if answers is None:
            raise socket.gaierror(socket.EAI_NONAME, f"unscripted host {host}")
        if family in (0, socket.AF_INET):
            self.passes[host] = self.passes.get(host, 0) + 1
        current = answers[min(self.passes[host], len(answers)) - 1]
        out = []
        for ip in current:
            fam = socket.AF_INET6 if ipaddress.ip_address(ip).version == 6 else socket.AF_INET
            if family in (0, fam):
                out.append((fam, type or socket.SOCK_STREAM, 6, "", (ip, 0)))
        if not out:
            raise socket.gaierror(socket.EAI_NODATA, f"no {family} records for {host}")
        return out


@pytest.fixture
def dns(monkeypatch) -> FakeDns:
    fake = FakeDns()
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return fake


class Recorder:
    """An ``httpx.MockTransport`` that remembers what actually reached the wire."""

    def __init__(self, respond=None) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond or (lambda request: httpx.Response(200, text="ok"))
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._respond(request)


# ── address policy ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "addr",
    [
        "64:ff9b::7f00:1",  # NAT64 → 127.0.0.1
        "64:ff9b::a9fe:a9fe",  # NAT64 → 169.254.169.254 (cloud metadata)
        "64:ff9b::a00:1",  # NAT64 → 10.0.0.1
        "64:ff9b::6464:64c8",  # NAT64 → 100.100.100.200 (Alibaba metadata, CGNAT)
    ],
)
def test_nat64_wrapped_internal_addresses_are_blocked(addr):
    assert is_blocked_ip(ipaddress.ip_address(addr)) is True


def test_nat64_wrapped_public_address_is_allowed():
    assert is_blocked_ip(ipaddress.ip_address("64:ff9b::808:808")) is False  # 8.8.8.8


def test_ingestion_keeps_the_policy_it_delegates_to_http_safe():
    """One policy, two names: the underscore aliases are what callers and tests patch."""
    assert ingestion._is_safe_url is http_safe.is_safe_url
    assert ingestion._is_blocked_ip is http_safe.is_blocked_ip


def test_preflight_matches_the_pinned_transport(dns):
    dns.set("mixed.example", [PUBLIC, "10.0.0.5"])
    dns.set("fine.example", [PUBLIC])
    assert is_safe_url("https://fine.example/x") is True
    assert is_safe_url("https://mixed.example/x") is False  # one private record poisons the name
    assert is_safe_url("ftp://fine.example/x") is False
    assert is_safe_url("https://localhost/x") is False
    assert is_safe_url("https://printer.local/x") is False


# ── shared TLS context ───────────────────────────────────────────────────────


def test_the_ssl_context_is_built_once_per_cert_environment(monkeypatch):
    import certifi

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setattr(http_safe, "_ctx_cache", {})
    built: list[int] = []
    real = httpx.create_ssl_context

    def counting(*a, **k):
        built.append(1)
        return real(*a, **k)

    monkeypatch.setattr(httpx, "create_ssl_context", counting)

    first = shared_ssl_context()
    assert shared_ssl_context() is first
    for _ in range(5):
        with make_client():  # real clients: none of them may trigger another build
            pass
    assert len(built) == 1

    monkeypatch.setenv("SSL_CERT_FILE", certifi.where())  # httpx honours it, so the cache must too
    assert shared_ssl_context() is not first
    assert len(built) == 2


def test_make_client_hands_out_the_shared_context_unless_overridden(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(httpx, "Client", lambda **kw: seen.append(kw))
    make_client(timeout=3)
    make_client(verify=False)
    assert seen[0]["verify"] is shared_ssl_context() and seen[0]["timeout"] == 3
    assert seen[1]["verify"] is False


def test_ssrf_safe_client_installs_the_pinned_transport(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(httpx, "Client", lambda **kw: seen.append(kw))
    ssrf_safe_client(timeout=7, follow_redirects=False)
    assert isinstance(seen[0]["transport"], PinnedTransport)
    assert seen[0]["verify"] is shared_ssl_context()
    assert seen[0]["timeout"] == 7 and seen[0]["follow_redirects"] is False
    with pytest.raises(TypeError):
        ssrf_safe_client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))


def test_no_module_builds_an_httpx_client_outside_the_factory():
    """A direct ``httpx.Client(...)`` pays the 48 ms CA-bundle load (and skips the SSRF option)."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    offenders = []
    for path in sorted(app_dir.rglob("*.py")):
        if path.name == "http_safe.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("Client", "AsyncClient")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "httpx"
            ):
                offenders.append(f"{path.relative_to(app_dir.parent)}:{node.lineno}")
    assert not offenders, "use services.http_safe.make_client / make_async_client / ssrf_safe_client: " + ", ".join(offenders)


# ── the pinned transport ─────────────────────────────────────────────────────


def _client(rec: Recorder, **kw) -> httpx.Client:
    return httpx.Client(transport=PinnedTransport(direct=rec.transport), **kw)


def test_the_request_goes_to_the_address_that_was_judged(dns):
    dns.set("paper.example", [PUBLIC])
    rec = Recorder()
    with _client(rec) as client:
        assert client.get("https://paper.example/doc?id=1").status_code == 200

    sent = rec.requests[0]
    assert sent.url.host == PUBLIC  # connects to the judged address…
    assert (sent.url.path, sent.url.query) == ("/doc", b"id=1")
    assert sent.headers["host"] == "paper.example"  # …but the server still sees the real name
    assert sent.extensions["sni_hostname"] == "paper.example"  # and TLS verifies that name
    assert dns.resolutions == 1  # one resolution pass, no second look-up to race against


def test_a_dns_answer_that_flips_after_the_preflight_is_refused(dns):
    """The attack: public for ``is_safe_url``, loopback for the connection."""
    dns.set("rebind.example", [PUBLIC], ["127.0.0.1"])
    rec = Recorder()
    assert is_safe_url("https://rebind.example/secret") is True  # pass 1: public
    with _client(rec) as client, pytest.raises(UnsafeAddressError, match="non-public"):
        client.get("https://rebind.example/secret")  # pass 2: loopback
    assert rec.requests == []


def test_a_name_with_one_private_record_is_refused_not_trusted_to_pick(dns):
    dns.set("mixed.example", [PUBLIC, "10.1.2.3"])
    rec = Recorder()
    with _client(rec) as client, pytest.raises(UnsafeAddressError):
        client.get("https://mixed.example/")
    assert rec.requests == []


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://169.254.169.254/latest/",
        "http://0.0.0.0/",
        "http://127.1/",  # shorthand form: only the OS resolver understands it …
        "http://2130706433/",  # … as it does the decimal one
    ],
)
def test_internal_literals_never_reach_the_wire(dns, url):
    dns.set("127.1", ["127.0.0.1"])
    dns.set("2130706433", ["127.0.0.1"])
    rec = Recorder()
    with _client(rec) as client, pytest.raises(UnsafeAddressError):
        client.get(url)
    assert rec.requests == []


def test_a_public_literal_is_used_as_is(dns):
    rec = Recorder()
    with _client(rec) as client:
        client.get(f"https://{PUBLIC}/x")
        client.get(f"http://[{PUBLIC_V6}]:8080/y")
    first, second = rec.requests
    assert first.url.host == PUBLIC and "sni_hostname" not in first.extensions
    assert second.url.host == PUBLIC_V6 and second.url.port == 8080
    assert second.url.netloc == f"[{PUBLIC_V6}]:8080".encode()
    assert dns.lookups == []  # literals are not looked up


def test_plain_http_carries_no_sni(dns):
    dns.set("plain.example", [PUBLIC])
    rec = Recorder()
    with _client(rec) as client:
        client.get("http://plain.example:8080/a")
    sent = rec.requests[0]
    assert sent.url.host == PUBLIC and sent.url.port == 8080
    assert sent.headers["host"] == "plain.example:8080"
    assert "sni_hostname" not in sent.extensions


def test_an_unresolvable_host_is_a_connect_error_not_an_unsafe_one(dns):
    rec = Recorder()
    with _client(rec) as client, pytest.raises(httpx.ConnectError, match="cannot resolve"):
        client.get("https://nowhere.example/")
    assert rec.requests == []


def test_the_next_address_is_tried_when_a_connection_fails(dns):
    dns.set("dual.example", [PUBLIC, PUBLIC_V6])

    def respond(request):
        if request.url.host == PUBLIC:
            raise httpx.ConnectError("no route", request=request)
        return httpx.Response(200, text="via v6")

    rec = Recorder(respond)
    with _client(rec) as client:
        assert client.get("https://dual.example/").text == "via v6"
    assert [r.url.host for r in rec.requests] == [PUBLIC, PUBLIC_V6]
    assert {r.headers["host"] for r in rec.requests} == {"dual.example"}


def test_every_address_failing_surfaces_the_last_connect_error(dns):
    dns.set("down.example", [PUBLIC, PUBLIC_V6])

    def respond(request):
        raise httpx.ConnectError(f"refused {request.url.host}", request=request)

    rec = Recorder(respond)
    with _client(rec) as client, pytest.raises(httpx.ConnectError, match="refused"):
        client.get("https://down.example/")
    assert len(rec.requests) == 2


def test_per_request_timeouts_survive_the_rewrite(dns):
    dns.set("slow.example", [PUBLIC])
    rec = Recorder()
    with _client(rec) as client:
        client.get("https://slow.example/", timeout=3.5)
    assert rec.requests[0].extensions["timeout"]["connect"] == 3.5


def test_every_redirect_hop_is_resolved_and_judged_again(dns):
    dns.set("start.example", [PUBLIC])
    dns.set("inside.example", ["10.0.0.5"])
    rec = Recorder(lambda r: httpx.Response(302, headers={"location": "http://inside.example/admin"}))
    with _client(rec, follow_redirects=True) as client, pytest.raises(UnsafeAddressError):
        client.get("https://start.example/go")
    assert [r.headers["host"] for r in rec.requests] == ["start.example"]  # the 2nd hop never left


def test_a_proxied_request_is_handed_to_the_proxy_unpinned(dns, monkeypatch):
    monkeypatch.setenv("https_proxy", "http://proxy.invalid:3128")
    monkeypatch.setenv("no_proxy", "internal-ok.example")
    direct, proxied = Recorder(), Recorder()
    handed: list[str] = []

    def via(proxy: str):
        handed.append(proxy)
        return proxied.transport

    with httpx.Client(transport=PinnedTransport(direct=direct.transport, via_proxy=via)) as client:
        client.get("https://far.example/a")  # proxy resolves the name, not us
        dns.set("internal-ok.example", [PUBLIC])
        client.get("https://internal-ok.example/b")  # NO_PROXY → direct and pinned
        dns.set("plain.example", [PUBLIC])
        client.get("http://plain.example/c")  # only https_proxy is set → direct and pinned

    assert handed == ["http://proxy.invalid:3128"]
    assert [r.url.host for r in proxied.requests] == ["far.example"]  # untouched
    assert [r.url.host for r in direct.requests] == [PUBLIC, PUBLIC]
    assert dns.resolutions == 2  # far.example was never looked up here


# ── against a real socket: Host header, no connection reuse, TLS name ─────────


class _Echo(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    seen: list[tuple[int, str]] = []

    def do_GET(self):  # noqa: N802
        type(self).seen.append((self.client_address[1], self.headers.get("Host") or ""))
        body = (self.headers.get("Host") or "").encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _serve(server: http.server.ThreadingHTTPServer) -> None:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()


@pytest.fixture
def echo_server():
    handler = type("Echo", (_Echo,), {"seen": []})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    _serve(server)
    yield server, handler
    server.shutdown()
    server.server_close()


def test_a_name_that_does_not_resolve_is_reached_through_the_pinned_address(echo_server, monkeypatch):
    server, handler = echo_server
    port = server.server_address[1]
    # Stand in for "the address that was judged public" with the loopback test server.
    monkeypatch.setattr(http_safe, "check_public_addresses", lambda host: [ipaddress.ip_address("127.0.0.1")])

    with ssrf_safe_client(timeout=5) as client:
        first = client.get(f"http://pinned.invalid:{port}/a")
        second = client.get(f"http://pinned.invalid:{port}/b")

    assert first.text == second.text == f"pinned.invalid:{port}"
    ports = {client_port for client_port, _host in handler.seen}
    assert len(ports) == 2, "idle connections must not be kept: the pool is keyed by IP, not by name"


def _self_signed(tmp_path: Path, name: str) -> tuple[str, str]:
    cryptography = pytest.importorskip("cryptography")
    del cryptography
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return str(cert_path), str(key_path)


def test_tls_is_verified_against_the_original_name_not_the_pinned_ip(tmp_path, monkeypatch):
    cert, key = _self_signed(tmp_path, "secure.test")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), type("Echo", (_Echo,), {"seen": []}))
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert, key)
    server.socket = server_ctx.wrap_socket(server.socket, server_side=True)
    _serve(server)
    port = server.server_address[1]
    monkeypatch.setattr(http_safe, "check_public_addresses", lambda host: [ipaddress.ip_address("127.0.0.1")])
    trust_it = ssl.create_default_context(cafile=cert)
    try:
        with ssrf_safe_client(timeout=5, verify=trust_it) as client:
            ok = client.get(f"https://secure.test:{port}/")
            assert ok.status_code == 200 and ok.text == f"secure.test:{port}"
            # Same socket, same certificate — but a different *name*: verification must fail.
            with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
                client.get(f"https://other.test:{port}/")
    finally:
        server.shutdown()
        server.server_close()


# ── callers: the rebinding timeline through the real entry points ─────────────


@pytest.fixture
def connects(monkeypatch) -> list:
    """Every attempt to reach the network: ``("http", host)`` for a request that got as far as an
    httpx transport, ``("socket", address)`` for a connect (refused, so nothing leaves the machine).

    "The fetch returned nothing" is not proof that nothing was attempted — an unpinned client that
    tries 127.0.0.1 and is refused also returns nothing, and the suite's own outbound guard
    refuses non-local hosts at the transport, hiding the attempt from the socket layer. The point
    of the pinned transport is that the request is never sent.
    """
    attempts: list = []

    def refuse(self, address):
        attempts.append(("socket", address))
        raise ConnectionRefusedError(errno.ECONNREFUSED, "test: nothing may connect")

    monkeypatch.setattr(socket.socket, "connect", refuse)

    reaches_the_network = httpx.HTTPTransport.handle_request

    def record(self, request):
        attempts.append(("http", request.url.host))
        return reaches_the_network(self, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", record)
    return attempts


def test_ingest_url_refuses_a_name_that_flips_to_loopback(dns, connects):
    dns.set("flip.example", [PUBLIC], ["127.0.0.1"])
    with pytest.raises(ValueError, match="public http"):
        ingestion.ingest_url("https://flip.example/article", persist=False)
    assert connects == []


def test_fetch_pdf_ex_reports_ssrf_for_a_name_that_flips_to_loopback(dns, connects):
    dns.set("flip.example", [PUBLIC], ["127.0.0.1"])
    data, reason = fetch_pdf_ex("https://flip.example/paper.pdf")
    assert (data, reason) == (None, "ssrf")
    assert connects == []


def test_fulltext_fetchers_drop_a_name_that_flips_to_loopback(dns, connects):
    dns.set("flip.example", [PUBLIC], ["127.0.0.1"])
    assert ft._fetch_landing_text("https://flip.example/landing", timeout=5) is None
    dns.set("flip2.example", [PUBLIC], ["127.0.0.1"])
    ev = ft.Evidence(source="web", identifier="https://flip2.example/page", title="t", snippet="s", relevance=0.5)
    assert ft._fetch_web_text(ev, timeout=5) is None
    assert connects == []


def test_the_patent_landing_fetch_cannot_be_pointed_inside(dns, connects):
    dns.set("patents.google.com", ["10.0.0.9"])  # poisoned resolver
    assert fetch_patent_landing("US1234567B2") is None
    assert connects == []
