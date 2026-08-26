"""Web Research error model (Phase 12B).

Typed, safe-to-log failures. Raw provider payloads, API keys, private IPs
and stack traces must never cross these boundaries into public output.
"""

from __future__ import annotations


class ResearchError(Exception):
    """Base class for research pipeline failures."""


class UnsafeUrlError(ResearchError):
    """URL rejected by the SSRF / scheme / userinfo policy."""


class FetchError(ResearchError):
    """A single fetch attempt failed (network, timeout, oversize, ...)."""
