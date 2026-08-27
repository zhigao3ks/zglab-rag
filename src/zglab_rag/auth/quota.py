"""Per-user usage protection (Phase 11C).

Logged-in users can still abuse cost-bearing endpoints, so every ask is
checked against a per-minute rate and a per-day quota before any LLM call
happens. Counters live in auth.db (per user / day / minute bucket), which
keeps them correct across process restarts without introducing Redis.

Recorded policy (hardening review): quota is counted only for requests
that pass authentication, CSRF, validation and the concurrency guard and
actually enter the cost-bearing workflow. Authentication/CSRF failures and
SERVICE_BUSY rejections never consume quota, and a quota-exceeded request
does not count itself. The check + increment runs inside a single
BEGIN IMMEDIATE transaction, so concurrent requests cannot race past a
check-then-increment window.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from zglab_rag.auth.errors import QuotaExceededError
from zglab_rag.auth.repositories import UsageRepository, utc_now


@dataclass(frozen=True, slots=True)
class QuotaConfig:
    requests_per_minute: int = 10
    requests_per_day: int = 100


class UsageGuard:
    def __init__(
        self,
        connection: sqlite3.Connection,
        config: QuotaConfig | None = None,
        *,
        table: str = "usage",
    ) -> None:
        self.connection = connection
        self.usage = UsageRepository(connection, table=table)
        self.config = config or QuotaConfig()

    def check_and_record(self, user_id: int, *, now: datetime | None = None) -> None:
        """Atomically count this request and raise when over limit.

        The increment happens first inside one write transaction; when the
        resulting count exceeds a limit the transaction is rolled back, so
        a rejected request never consumes quota. BEGIN IMMEDIATE serializes
        concurrent writers and removes the check-then-increment race.
        """
        moment = now or utc_now()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.usage.record(user_id, now=moment)
            minute_count, day_count = self.usage.counts(user_id, now=moment)
            if minute_count > self.config.requests_per_minute:
                self.connection.rollback()
                next_minute = moment.replace(second=0, microsecond=0) + timedelta(minutes=1)
                raise QuotaExceededError(max(0.1, (next_minute - moment).total_seconds()))
            if day_count > self.config.requests_per_day:
                self.connection.rollback()
                next_day = (moment + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                raise QuotaExceededError(max(0.1, (next_day - moment).total_seconds()))
            self.connection.commit()
        except QuotaExceededError:
            raise
        except Exception:
            self.connection.rollback()
            raise

    def refund(self, user_id: int, *, now: datetime | None = None) -> None:
        """Give one request back after a recorded call could not proceed.

        Used when the generation task cannot be submitted (graceful
        shutdown race) after quota was already counted, so the user is not
        charged for work that never started.
        """
        self.usage.refund(user_id, now=now or utc_now())

    @staticmethod
    def prune_old_usage(connection: sqlite3.Connection, *, keep_days: int = 7) -> int:
        cutoff = (utc_now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        return UsageRepository(connection).prune_old(before_day=cutoff)
