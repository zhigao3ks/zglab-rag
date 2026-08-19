"""Process-local rate limiter for the public API.

The limiter uses a simple sliding window algorithm per client identity.
It is suitable for single-instance deployments; distributed rate limiting
requires external infrastructure (e.g., Redis) and is deferred to Phase 10.

Client identity is currently derived from request.client.host. In production
behind a trusted proxy, this should be X-Forwarded-For (Phase 10).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field


class RateLimitExceededError(RuntimeError):
    """Raised when a client exceeds the rate limit."""

    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Rate limit exceeded; retry after {retry_after_seconds:.1f}s")


@dataclass
class RateLimiter:
    """Process-local sliding window rate limiter.

    The limiter tracks request timestamps per client and enforces a maximum
    number of requests within a sliding window.
    """

    max_requests: int
    window_seconds: int
    _requests: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _clock: callable = field(default_factory=lambda: time.time)  # type: ignore[assignment]

    def check(self, client_id: str) -> None:
        """Check if the client is within the rate limit.

        Raises RateLimitExceededError if the limit is exceeded. The error
        includes a retry_after hint based on the oldest request in the window.
        """
        now = self._clock()
        window_start = now - self.window_seconds

        with self._lock:
            # Prune old requests outside the window
            timestamps = self._requests[client_id]
            self._requests[client_id] = [t for t in timestamps if t > window_start]
            timestamps = self._requests[client_id]

            if len(timestamps) >= self.max_requests:
                # Calculate retry_after based on the oldest request in window
                oldest = min(timestamps)
                retry_after = oldest + self.window_seconds - now
                raise RateLimitExceededError(max(0.1, retry_after))

            # Record this request
            timestamps.append(now)

    def reset(self, client_id: str | None = None) -> None:
        """Reset rate limit state. Primarily for testing."""
        with self._lock:
            if client_id is None:
                self._requests.clear()
            else:
                self._requests.pop(client_id, None)

    def remaining(self, client_id: str) -> int:
        """Return the number of requests remaining in the current window.

        Note: This is a snapshot and may change immediately in concurrent use.
        """
        now = self._clock()
        window_start = now - self.window_seconds
        with self._lock:
            timestamps = self._requests.get(client_id, [])
            active = [t for t in timestamps if t > window_start]
            return max(0, self.max_requests - len(active))
