"""URL safety & SSRF boundary (Phase 12B).

Everything that turns an untrusted URL string into a safe-to-fetch target
lives here. Rules are deterministic and fail-closed:

- scheme allowlist: http/https only (https recommended in production);
- URLs with embedded credentials (user:pass@) are rejected outright;
- hostnames are DNS-resolved and EVERY resolved address must be a public
  address; a single unsafe address rejects the whole name (no mixing);
- IP literals are classified directly, never fetched blindly;
- classification is based on ``ipaddress`` predicates (is_loopback /
  is_private / is_link_local / is_reserved / is_multicast / is_unspecified)
  plus an explicit cloud metadata range — not on string blacklists;
- IPv4-mapped IPv6 addresses (::ffff:a.b.c.d) are unwrapped before
  classification so they cannot smuggle a private IPv4 through.

The fetcher pins connections to a validated address, which mitigates
first-order DNS rebinding between validation and connect. Every redirect
hop re-runs the full validation (see fetch.py).
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from zglab_rag.research.errors import UnsafeUrlError

ALLOWED_SCHEMES = ("http", "https")
DEFAULT_PORTS = {"http": 80, "https": 443}
# Common tracking parameters stripped during canonicalization only; the
# original search-result URL is always preserved for provenance.
TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
}
# AWS IMDS and the general link-local metadata neighborhood. Link-local is
# already rejected by ipaddress predicates; this documents the explicit
# intent and covers future provider-specific metadata IPs. CGNAT
# (100.64/10) is refused explicitly regardless of Python version details.
METADATA_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fd00:ec2::254/128"),
    ipaddress.ip_network("100.64.0.0/10"),
)


def canonicalize_url(url: str) -> str:
    """Normalize a URL for dedupe/comparison (never for fetching as-is).

    Lowercases scheme/host, removes the fragment, drops default ports and
    common tracking params. Raises UnsafeUrlError for structurally invalid
    or disallowed URLs.
    """
    parts = _split_checked(url)
    host = parts.hostname or ""
    port = parts.port
    netloc = host
    if port is not None and port != DEFAULT_PORTS.get(parts.scheme):
        netloc = f"{host}:{port}"
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
    ]
    return urlunsplit((parts.scheme, netloc, parts.path or "/", urlencode(query_pairs), ""))


def _split_checked(url: str):
    if not isinstance(url, str) or not url.strip():
        raise UnsafeUrlError("empty url")
    try:
        parts = urlsplit(url.strip())
    except ValueError as exc:
        raise UnsafeUrlError("unparseable url") from exc
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"scheme '{scheme or 'none'}' not allowed")
    if parts.username is not None or parts.password is not None:
        raise UnsafeUrlError("embedded credentials not allowed")
    if not parts.hostname:
        raise UnsafeUrlError("missing host")
    return parts


@dataclass(frozen=True, slots=True)
class SafeTarget:
    """A validated fetch target: URL plus the addresses it resolved to."""

    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


class DnsResolver:
    """Default resolver; tests inject a fake (never hit public DNS in CI)."""

    def resolve(self, host: str, port: int) -> list[str]:
        try:
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except OSError as exc:
            raise UnsafeUrlError("dns resolution failed") from exc
        addresses = sorted({info[4][0] for info in infos})
        if not addresses:
            raise UnsafeUrlError("dns returned no addresses")
        return addresses


def is_safe_address(raw: str) -> bool:
    """True only for unambiguous public addresses."""
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if any(ip in network for network in METADATA_NETWORKS):
        return False
    return not (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_fetch_target(
    url: str,
    resolver: DnsResolver | None = None,
    *,
    allowed_schemes: tuple[str, ...] = ALLOWED_SCHEMES,
) -> SafeTarget:
    """Full URL + DNS safety validation for one fetch hop.

    Rejects non-allowlisted schemes, userinfo URLs, and any hostname whose
    resolved address set contains even one non-public address. IP-literal
    hosts are classified directly. Raises UnsafeUrlError on any violation.
    """
    parts = _split_checked(url)
    if parts.scheme not in allowed_schemes:
        raise UnsafeUrlError(f"scheme '{parts.scheme}' not allowed")
    host = parts.hostname or ""
    port = parts.port or DEFAULT_PORTS[parts.scheme]

    # IP literals: classify directly (covers bracketed IPv6 via hostname).
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not is_safe_address(str(literal)):
            raise UnsafeUrlError("ip literal address not allowed")
        return SafeTarget(url=url, host=host, port=port, addresses=(str(literal),))

    resolver = resolver or DnsResolver()
    addresses = tuple(resolver.resolve(host, port))
    unsafe = [address for address in addresses if not is_safe_address(address)]
    if unsafe:
        # Conservative default: a hostname with ANY unsafe resolved address
        # is refused entirely (mixed public+private answers are a red flag).
        raise UnsafeUrlError("hostname resolves to non-public addresses")
    return SafeTarget(url=url, host=host, port=port, addresses=addresses)
