"""Capability error model (Phase 12A).

The layer separates three outcome classes so future policy (e.g. whether
to fall back to web research) can reason safely:

- business insufficient  -> CapabilityStatus.INSUFFICIENT_EVIDENCE result
- technical failure      -> CapabilityTechnicalError raised
- policy rejection       -> CapabilityPolicyError raised

Technical errors wrap the original exception verbatim: the API boundary
unwraps it before error mapping, so Phase 9 public error codes
(PROVIDER_UNAVAILABLE / INTERNAL_ERROR / GENERATION_TIMEOUT) stay exact.
An LLM outage must never be misread as "knowledge insufficient".
"""

from __future__ import annotations


class CapabilityError(Exception):
    """Base class for capability-layer failures."""


class CapabilityTechnicalError(CapabilityError):
    """Infrastructure failure inside a capability (LLM down, DB error, ...).

    Carries the original exception so the API can restore the exact
    Phase 9 error mapping; this is NOT evidence insufficiency.
    """

    def __init__(self, message: str, *, original: BaseException | None = None) -> None:
        super().__init__(message)
        self.original = original


class CapabilityPolicyError(CapabilityError):
    """The capability was refused by policy before doing any work.

    Unused in Phase 12A (quota / kill switch live in the security
    gateway); defined so future capability-level policy has a typed home
    that is distinct from both business and technical outcomes.
    """


class DuplicateCapabilityError(CapabilityError):
    """A capability with the same id is already registered."""


class CapabilityNotFoundError(CapabilityError):
    """No capability is registered under the requested id."""


def unwrap_capability_error(exc: BaseException) -> BaseException:
    """Return the original exception behind a CapabilityTechnicalError.

    Callers that map technical failures to public error codes must use the
    wrapped original, keeping the pre-Phase-12 behavior bit-for-bit.
    """
    if isinstance(exc, CapabilityTechnicalError) and exc.original is not None:
        return exc.original
    return exc
