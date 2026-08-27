"""Pinned-resolution fetch transport (Phase 12D).

Closes the Phase 12B DNS rebinding / TOCTOU window: the IP addresses used
for the security decision are the exact addresses the TCP connection is
established to.

Mechanism: after ``validate_fetch_target`` resolves and classifies a host,
the fetcher pins ``host -> validated IPs`` into a ``PinnedHosts`` registry.
The transport's httpcore network backend then refuses to resolve anything
on its own — ``connect_tcp`` only connects to one of the pinned, re-checked
addresses. A rebinding attacker who flips DNS between validation and
connect can therefore never steer the connection to a different address.

Preserved security semantics:
- TLS hostname verification / SNI: httpcore performs ``start_tls`` with the
  original origin host, not the pinned IP literal, so certificate checks
  stay hostname-based (never ``verify=False``);
- Host header correctness: the request URL keeps the original host;
- redirect revalidation: every hop runs ``validate_fetch_target`` again and
  re-pins the new host before its connection.
"""

from __future__ import annotations

import httpcore
import httpx

from zglab_rag.research.url_safety import is_safe_address


class PinnedHosts:
    """Per-fetch registry of validated host -> IP pins (fail closed)."""

    def __init__(self) -> None:
        self._pins: dict[str, tuple[str, ...]] = {}

    def pin(self, host: str, addresses: tuple[str, ...]) -> None:
        self._pins[host.lower()] = tuple(addresses)

    def lookup(self, host: str) -> tuple[str, ...] | None:
        return self._pins.get(host.lower())

    def clear(self) -> None:
        self._pins.clear()


class PinnedResolutionBackend(httpcore.NetworkBackend):
    """Network backend that only connects to pre-validated pinned IPs.

    Any host without pins, or whose pins no longer classify as safe public
    addresses, is refused before any socket work happens.
    """

    def __init__(
        self,
        pins: PinnedHosts,
        wrapped: httpcore.NetworkBackend | None = None,
    ) -> None:
        self._pins = pins
        self._wrapped = wrapped or httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ) -> httpcore.NetworkStream:
        hostname = host.decode("ascii") if isinstance(host, bytes) else host
        addresses = self._pins.lookup(hostname)
        if not addresses:
            raise httpcore.ConnectError(
                f"connection to unpinned host refused: {hostname}"
            )
        safe = [address for address in addresses if is_safe_address(address)]
        if not safe:
            raise httpcore.ConnectError(
                f"pinned addresses no longer safe for host: {hostname}"
            )
        target = safe[0]
        return self._wrapped.connect_tcp(
            target,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )


def build_pinned_transport(pins: PinnedHosts) -> httpx.HTTPTransport:
    """Standard httpx transport whose connections go through the pins.

    TLS context, limits, retries and hostname verification all stay the
    stock httpx defaults; only the address resolution path is replaced.
    The pin swap targets httpcore's documented extension point
    (``ConnectionPool(network_backend=...)``) via the pool attribute that
    httpx 0.28 / httpcore 1.0 construct internally.
    """
    transport = httpx.HTTPTransport()
    transport._pool._network_backend = PinnedResolutionBackend(pins)
    return transport
