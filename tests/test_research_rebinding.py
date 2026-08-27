"""Phase 12D tests: DNS rebinding blocker (pinned-resolution transport).

Offline: the real httpx/httpcore stack runs against a scripted fake
network backend, so assertions cover the exact connect targets and TLS
SNI semantics without any real network traffic.

Core scenario (task §24): validation resolves a public address A; a
rebinding attacker would flip DNS before connect to private B. With
pinned resolution the connection can only go to A — the backend never
performs its own lookup.
"""

from __future__ import annotations

import httpcore
import pytest

from tests.test_research_safety import PUBLIC, FakeResolver
from zglab_rag.research.contracts import FetchFailureReason, ResearchBudget
from zglab_rag.research.fetch import SafeFetcher
from zglab_rag.research.pinned_transport import (
    PinnedHosts,
    PinnedResolutionBackend,
)

PUBLIC_B = "93.184.216.35"
PRIVATE_B = "10.0.0.1"
LONG_PARAGRAPH = "这是一段足够长的正文内容，用来通过最小质量检查。" * 10


class RecordingBackend(httpcore.NetworkBackend):
    """Stands in for the OS resolver + socket layer.

    If pinning failed and httpcore resolved DNS itself, this fake would
    receive a hostname; with pinning it must always receive the validated
    IP literal. Every connect target is recorded for assertions.
    """

    def __init__(self, responses: dict[str, bytes]) -> None:
        self.connects: list[tuple[str, int]] = []
        self.responses = responses
        self.streams: list[ScriptedStream] = []

    def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ) -> httpcore.NetworkStream:
        target = host.decode("ascii") if isinstance(host, bytes) else host
        self.connects.append((target, port))
        body = self.responses.get(target, b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
        stream = ScriptedStream(body)
        self.streams.append(stream)
        return stream


class ScriptedStream(httpcore.NetworkStream):
    """Minimal canned HTTP stream speaking just enough for httpcore/h11."""

    def __init__(self, payload: bytes) -> None:
        self._buffer = payload
        self._closed = False
        self.tls_server_hostnames: list[str] = []

    def read(self, max_bytes: int, timeout=None) -> bytes:
        chunk, self._buffer = self._buffer[:max_bytes], self._buffer[max_bytes:]
        return chunk

    def write(self, buffer: bytes, timeout=None) -> None:
        pass

    def close(self) -> None:
        self._closed = True

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.tls_server_hostnames.append(server_hostname)
        raise httpcore.ConnectError("offline test stops before real TLS bytes")

    def get_extra_info(self, name):
        return None


def _html(body: str = LONG_PARAGRAPH) -> bytes:
    page = (
        "<html><head><title>t</title></head>"
        f"<body><article><p>{body}</p></article></body></html>"
    ).encode()
    return (
        b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
        b"Content-Length: " + str(len(page)).encode("ascii") + b"\r\n\r\n" + page
    )


def _redirect(location: str) -> bytes:
    return (
        b"HTTP/1.1 302 Found\r\nLocation: "
        + location.encode("ascii")
        + b"\r\nContent-Length: 0\r\n\r\n"
    )


def _fetch_with_backend(
    resolver: FakeResolver, backend: RecordingBackend, url: str
):
    """Run one SafeFetcher.fetch through the pinned transport, observing
    connects via the recording backend (patch stays live during fetch)."""
    import zglab_rag.research.fetch as fetch_module

    original = fetch_module.build_pinned_transport

    def factory(pins):
        transport = original(pins)
        transport._pool._network_backend = PinnedResolutionBackend(
            pins, wrapped=backend
        )
        return transport

    fetch_module.build_pinned_transport = factory
    try:
        return SafeFetcher(ResearchBudget(), resolver=resolver).fetch(url)
    finally:
        fetch_module.build_pinned_transport = original


# ---------------------------------------------------------------------------
# Backend unit tests
# ---------------------------------------------------------------------------


def test_backend_connects_only_to_pinned_validated_ip() -> None:
    pins = PinnedHosts()
    pins.pin("example.com", (PUBLIC,))
    inner = RecordingBackend({PUBLIC: _html()})
    backend = PinnedResolutionBackend(pins, wrapped=inner)

    backend.connect_tcp("example.com", 443)
    # The rebind target B is never reached: connect went to validated A.
    assert inner.connects == [(PUBLIC, 443)]


def test_backend_refuses_unpinned_host_before_any_connect() -> None:
    pins = PinnedHosts()
    inner = RecordingBackend({})
    backend = PinnedResolutionBackend(pins, wrapped=inner)

    with pytest.raises(httpcore.ConnectError, match="unpinned"):
        backend.connect_tcp("evil.example", 443)
    assert inner.connects == []


def test_backend_refuses_pins_that_are_no_longer_safe() -> None:
    pins = PinnedHosts()
    pins.pin("example.com", (PRIVATE_B,))  # defensive: stale/private pin
    inner = RecordingBackend({})
    backend = PinnedResolutionBackend(pins, wrapped=inner)

    with pytest.raises(httpcore.ConnectError, match="no longer safe"):
        backend.connect_tcp("example.com", 443)
    assert inner.connects == []


# ---------------------------------------------------------------------------
# End-to-end through real httpx + httpcore with the pinned backend
# ---------------------------------------------------------------------------


def test_rebinding_attack_cannot_reach_private_address() -> None:
    """Validation sees public A; the attacker's later DNS (private B) is
    never consulted — the connection goes to A only."""
    resolver = FakeResolver({"example.com": [PUBLIC]})
    backend = RecordingBackend({PUBLIC: _html()})

    outcome = _fetch_with_backend(resolver, backend, "http://example.com/a")
    assert outcome.ok
    assert backend.connects == [(PUBLIC, 80)]
    assert PRIVATE_B not in [target for target, _port in backend.connects]


def test_https_sni_keeps_original_hostname() -> None:
    resolver = FakeResolver({"example.com": [PUBLIC]})
    backend = RecordingBackend({PUBLIC: _html()})

    outcome = _fetch_with_backend(resolver, backend, "https://example.com/a")
    # TLS cannot complete offline; the fetch fails safely AFTER proving
    # the connect target and the SNI hostname.
    assert not outcome.ok
    assert outcome.reason == FetchFailureReason.FETCH_ERROR
    assert backend.connects == [(PUBLIC, 443)]
    # SNI / TLS verification stays hostname-based, never the IP literal.
    assert backend.streams[0].tls_server_hostnames == ["example.com"]


def test_redirect_hops_get_revalidated_and_repinned() -> None:
    resolver = FakeResolver(
        {"example.com": [PUBLIC], "other.org": [PUBLIC_B]}
    )
    backend = RecordingBackend(
        {PUBLIC: _redirect("http://other.org/b"), PUBLIC_B: _html()}
    )

    outcome = _fetch_with_backend(resolver, backend, "http://example.com/a")
    assert outcome.ok
    assert outcome.page is not None
    assert outcome.page.final_url == "http://other.org/b"
    # Hop 1 connected to example.com's validated IP, hop 2 to other.org's
    # own freshly validated IP — pins are per-hop, never reused blindly.
    assert backend.connects == [(PUBLIC, 80), (PUBLIC_B, 80)]


def test_redirect_to_unvalidated_host_is_rejected_before_connect() -> None:
    resolver = FakeResolver({"example.com": [PUBLIC]})  # other.org unknown
    backend = RecordingBackend({PUBLIC: _redirect("http://other.org/b")})

    outcome = _fetch_with_backend(resolver, backend, "http://example.com/a")
    assert not outcome.ok
    assert outcome.reason == FetchFailureReason.SSRF_REJECTED
    assert backend.connects == [(PUBLIC, 80)]
