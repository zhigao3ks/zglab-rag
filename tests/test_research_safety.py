"""Phase 12B tests: URL canonicalization & SSRF safety boundary.

Fully offline: DNS is faked; no public network is touched. Covers the
mandatory SSRF matrix (loopback / private / link-local / metadata / IPv6 /
IPv4-mapped / scheme confusion / userinfo / rebinding-style mixed answers)
and URL parser edge cases.
"""

from __future__ import annotations

import pytest

from zglab_rag.research.errors import UnsafeUrlError
from zglab_rag.research.url_safety import (
    canonicalize_url,
    is_safe_address,
    validate_fetch_target,
)


class FakeResolver:
    """Deterministic resolver double; never touches public DNS."""

    def __init__(self, table: dict[str, list[str]]) -> None:
        self.table = table
        self.calls: list[str] = []

    def resolve(self, host: str, port: int) -> list[str]:
        self.calls.append(host)
        if host not in self.table:
            raise UnsafeUrlError("dns resolution failed")
        return list(self.table[host])


PUBLIC = "93.184.216.34"


def _resolver(**entries: list[str]) -> FakeResolver:
    return FakeResolver(dict(entries))


# ---------------------------------------------------------------------------
# IP classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.5.6.7",
        "0.0.0.0",
        "10.0.0.1",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "169.254.0.1",
        "100.64.0.1",  # CGNAT (is_private in Python 3.12 ipaddress)
        "::1",
        "fe80::1",  # IPv6 link-local
        "fd12::1",  # IPv6 unique-local private
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:10.0.0.1",  # IPv4-mapped private
        "::",  # unspecified
        "ff02::1",  # multicast
        "240.0.0.1",  # reserved
    ],
)
def test_non_public_addresses_rejected(address: str) -> None:
    assert is_safe_address(address) is False


@pytest.mark.parametrize("address", ["93.184.216.34", "8.8.8.8", "2606:2800:220:1::248"])
def test_public_addresses_allowed(address: str) -> None:
    assert is_safe_address(address) is True


def test_garbage_address_rejected() -> None:
    assert is_safe_address("not-an-ip") is False


# ---------------------------------------------------------------------------
# Canonicalization / URL parser edge cases
# ---------------------------------------------------------------------------


def test_canonicalize_scheme_and_host_case() -> None:
    assert canonicalize_url("HTTPS://EXAMPLE.com/Path") == "https://example.com/Path"


def test_canonicalize_removes_fragment_and_default_port() -> None:
    assert canonicalize_url("https://example.com:443/path#frag") == "https://example.com/path"
    assert canonicalize_url("http://example.com:80/x") == "http://example.com/x"
    # Non-default ports are kept.
    assert canonicalize_url("https://example.com:8443/x") == "https://example.com:8443/x"


def test_canonicalize_strips_tracking_params_only() -> None:
    canonical = canonicalize_url(
        "https://example.com/a?utm_source=x&utm_medium=y&id=42&fbclid=z"
    )
    assert canonical == "https://example.com/a?id=42"


def test_canonicalize_duplicate_detection() -> None:
    a = canonicalize_url("https://example.com/page?utm_source=one")
    b = canonicalize_url("https://EXAMPLE.com/page#frag")
    assert a == b


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "data:text/html,<script>",
        "javascript:alert(1)",
        "https://user:pass@example.com/",
        "example.com/no-scheme",
        "",
        "https://",
    ],
)
def test_canonicalize_rejects_disallowed_urls(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        canonicalize_url(url)


# ---------------------------------------------------------------------------
# Fetch target validation (URL + DNS)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "https://127.0.0.1.nip.io/",  # resolves to loopback (literal-like host)
        "http://0.0.0.0/",
        "http://[::1]/",
        "http://10.0.0.1/",
        "http://172.16.5.5/",
        "http://192.168.0.10/",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "ftp://example.com/data",
        "https://user:pass@example.com/",
    ],
)
def test_validate_rejects_ssrf_ip_literals_and_schemes(url: str) -> None:
    resolver = _resolver()
    if "nip.io" in url:
        resolver.table["127.0.0.1.nip.io"] = ["127.0.0.1"]
    with pytest.raises(UnsafeUrlError):
        validate_fetch_target(url, resolver)


def test_validate_hostname_resolving_private_rejected() -> None:
    resolver = _resolver(**{"internal.example": ["192.168.1.5"]})
    with pytest.raises(UnsafeUrlError):
        validate_fetch_target("https://internal.example/secret", resolver)


def test_validate_mixed_public_private_dns_rejected() -> None:
    # ANY unsafe resolved address rejects the whole hostname (conservative).
    resolver = _resolver(**{"mixed.example": [PUBLIC, "10.0.0.9"]})
    with pytest.raises(UnsafeUrlError):
        validate_fetch_target("https://mixed.example/", resolver)


def test_validate_dns_failure_rejected() -> None:
    resolver = _resolver()  # unknown host -> resolution failure
    with pytest.raises(UnsafeUrlError):
        validate_fetch_target("https://no-such-host.example/", resolver)


def test_validate_public_hostname_ok_and_returns_addresses() -> None:
    resolver = _resolver(**{"example.com": [PUBLIC]})
    target = validate_fetch_target("https://example.com/a", resolver)
    assert target.addresses == (PUBLIC,)
    assert target.host == "example.com"
    assert target.port == 443


def test_validate_ipv4_mapped_dns_rejected() -> None:
    resolver = _resolver(**{"sneaky.example": ["::ffff:127.0.0.1"]})
    with pytest.raises(UnsafeUrlError):
        validate_fetch_target("https://sneaky.example/", resolver)


def test_validate_https_only_policy() -> None:
    resolver = _resolver(**{"example.com": [PUBLIC]})
    with pytest.raises(UnsafeUrlError):
        validate_fetch_target(
            "http://example.com/", resolver, allowed_schemes=("https",)
        )
    target = validate_fetch_target(
        "https://example.com/", resolver, allowed_schemes=("https",)
    )
    assert target.port == 443
