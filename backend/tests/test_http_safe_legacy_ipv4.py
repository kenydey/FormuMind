"""An IPv4 address in an unusual spelling is judged by us, not by the operating system's resolver (round-4).

``test_ingest_ssrf`` failed on a real Windows runner for ``http://127.1/``, ``http://2130706433/`` and
``http://0177.0.0.1/``: glibc's ``getaddrinfo`` reads them as 127.0.0.1 (so Linux CI blocked them), Windows' does not
— it fails, ``resolve_addresses`` returned nothing, and the URL passed the pre-flight to an attempted connection.
"""
from __future__ import annotations

import socket

import pytest

from app.services import http_safe


@pytest.mark.parametrize(
    "host, expected",
    [
        ("127.1", "127.0.0.1"),
        ("2130706433", "127.0.0.1"),
        ("0177.0.0.1", "127.0.0.1"),
        ("0x7f.1", "127.0.0.1"),
        ("0x7f000001", "127.0.0.1"),
        ("017700000001", "127.0.0.1"),
        ("10.1", "10.0.0.1"),
        ("192.168.257", "192.168.1.1"),
        ("169.254.43518", "169.254.169.254"),
        ("1.2.3.4", "1.2.3.4"),
    ],
)
def test_legacy_spellings_are_decoded(host, expected):
    assert str(http_safe.parse_legacy_ipv4(host)) == expected


@pytest.mark.parametrize(
    "host",
    ["example.com", "", "08.1", "1.2.3.4.5", "256.1.1.1", "1.2.3.256", "4294967296", "0x1ffffffff", "a.b", "1..2", ".1", "1e3", "١٢٧.١"],
)
def test_other_hosts_are_left_to_the_resolver(host):
    assert http_safe.parse_legacy_ipv4(host) is None


@pytest.mark.parametrize("host", ["127.1", "2130706433", "0177.0.0.1", "0x7f.1", "10.1", "169.254.43518"])
def test_disguised_private_addresses_are_refused_even_when_the_resolver_cannot_read_them(host, monkeypatch):
    def windows_like(*args, **kwargs):
        raise socket.gaierror(11001, "getaddrinfo failed")

    monkeypatch.setattr(socket, "getaddrinfo", windows_like)
    assert http_safe.is_safe_url(f"http://{host}/admin") is False
    with pytest.raises(http_safe.UnsafeAddressError):
        http_safe.check_public_addresses(host)


def test_a_public_address_in_disguise_is_allowed(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(socket.gaierror(1, "no")))
    assert http_safe.is_safe_url("http://16909060/") is True  # 1.2.3.4
