"""Login throttling with two independent dimensions (Phase 11).

Login attempts are limited per source IP and per username with separate
windows. The throttle is process-local (single-instance deployment); it is
deliberately separate from the ask IP rate limiter so that ask traffic can
never starve login protection or vice versa.

Attempts are recorded before credential verification, so a storm of bad
passwords is bounded regardless of outcome.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field

from zglab_rag.auth.errors import LoginThrottledError


@dataclass
class LoginThrottleConfig:
    per_ip_attempts: int = 10
    per_ip_window_seconds: int = 600
    per_username_attempts: int = 5
    per_username_window_seconds: int = 900


@dataclass
class LoginThrottle:
    """Sliding-window login attempt tracker per IP and per username."""

    config: LoginThrottleConfig = field(default_factory=LoginThrottleConfig)
    _attempts: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _clock: callable = field(default_factory=lambda: time.time)  # type: ignore[assignment]

    def check_and_record(self, *, ip: str, username: str) -> None:
        """Record one attempt and raise LoginThrottledError when over limit.

        Both dimensions are evaluated before the attempt is counted for
        either; a rejection therefore does not extend the penalty window.
        """
        now = self._clock()
        ip_key = f"ip:{ip}"
        user_key = f"user:{username.lower()}"
        with self._lock:
            ip_times = self._prune(ip_key, now, self.config.per_ip_window_seconds)
            user_times = self._prune(user_key, now, self.config.per_username_window_seconds)
            if len(user_times) >= self.config.per_username_attempts:
                retry_after = self._retry_after(
                    user_times, self.config.per_username_window_seconds, now
                )
                raise LoginThrottledError(retry_after)
            if len(ip_times) >= self.config.per_ip_attempts:
                retry_after = self._retry_after(
                    ip_times, self.config.per_ip_window_seconds, now
                )
                raise LoginThrottledError(retry_after)
            ip_times.append(now)
            user_times.append(now)

    def _prune(self, key: str, now: float, window_seconds: int) -> list[float]:
        window_start = now - window_seconds
        times = [t for t in self._attempts[key] if t > window_start]
        self._attempts[key] = times
        return times

    @staticmethod
    def _retry_after(times: list[float], window_seconds: int, now: float) -> float:
        oldest = min(times)
        return max(0.1, oldest + window_seconds - now)

    def reset(self) -> None:
        """Clear all throttle state. Primarily for testing."""
        with self._lock:
            self._attempts.clear()
