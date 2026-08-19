"""Process-local concurrency guard for the public API.

The guard limits the number of concurrent generation requests. When all
slots are occupied, new requests are rejected immediately with
SERVICE_BUSY instead of queuing indefinitely.

This is a simple in-process guard suitable for single-instance deployments.
Distributed rate limiting is deferred to Phase 10 or later.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


class ServiceBusyError(RuntimeError):
    """Raised when all concurrent request slots are occupied."""


@dataclass
class ConcurrencyGuard:
    """Process-local concurrency guard using a semaphore.

    The guard is thread-safe and suitable for FastAPI's threadpool execution.
    """

    max_concurrent: int
    _semaphore: threading.Semaphore = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._semaphore = threading.Semaphore(self.max_concurrent)

    def acquire(self) -> None:
        """Try to acquire a slot without blocking.

        Raises ServiceBusyError if all slots are occupied.
        """
        if not self._semaphore.acquire(blocking=False):
            raise ServiceBusyError(
                f"All {self.max_concurrent} concurrent request slots are occupied"
            )

    def release(self) -> None:
        """Release a slot after request completion."""
        self._semaphore.release()

    @property
    def available_slots(self) -> int:
        """Return the number of currently available slots.

        Note: This is a snapshot and may change immediately in concurrent use.
        """
        # Semaphore._value is the internal counter; this is for testing/diagnostics.
        return getattr(self._semaphore, "_value", 0)
