"""Safe fetch (Phase 12B).

Fetches one candidate URL under hard bounds, with manual redirect control.
Every hop (including the first URL) runs the full URL + DNS SSRF
validation; redirect targets are resolved relative to the current URL,
canonicalized and re-validated before any further request.

Hardening properties:
- httpx client with follow_redirects disabled; hop count bounded;
- no cookie jar, no credentials, no session state ever attached;
- bounded streaming read: once ``max_response_bytes`` (decompressed) is
  exceeded the fetch aborts — no unbounded ``response.content`` reads;
- content-type allowlist decided from the response header, never from the
  file extension;
- connect + read timeout per request; overall budget checked by the caller.

Residual risk (documented): validation and connect are not atomic, so a
determined DNS-rebinding attacker could still race the window; IP-pinned
connections with proper TLS hostname handling are future work.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin

import httpx

from zglab_rag.research.contracts import FetchFailureReason, ResearchBudget
from zglab_rag.research.errors import UnsafeUrlError
from zglab_rag.research.url_safety import ALLOWED_SCHEMES, DnsResolver, validate_fetch_target


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """Successfully fetched page body with full provenance."""

    final_url: str
    content_type: str
    text: str
    redirect_chain: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class FetchOutcome:
    """Exactly one of page / reason is set."""

    page: FetchedPage | None = None
    reason: FetchFailureReason | None = None

    @property
    def ok(self) -> bool:
        return self.page is not None


class SafeFetcher:
    """Bounded, SSRF-checked fetcher. Sync by design (thread-executor model)."""

    def __init__(
        self,
        budget: ResearchBudget,
        *,
        resolver: DnsResolver | None = None,
        transport: httpx.BaseTransport | None = None,
        allowed_schemes: tuple[str, ...] = ALLOWED_SCHEMES,
    ) -> None:
        self._budget = budget
        self._resolver = resolver
        self._transport = transport
        self._allowed_schemes = tuple(scheme.lower() for scheme in allowed_schemes)

    def fetch(self, url: str) -> FetchOutcome:
        """Fetch one URL following the redirect policy. Never raises."""
        try:
            return self._fetch_or_fail(url)
        except _FetchRejected as rejected:
            return FetchOutcome(reason=rejected.reason)
        except httpx.TimeoutException:
            return FetchOutcome(reason=FetchFailureReason.TIMEOUT)
        except httpx.HTTPError:
            return FetchOutcome(reason=FetchFailureReason.FETCH_ERROR)

    def _fetch_or_fail(self, url: str) -> FetchOutcome:
        current_url = url
        chain: list[str] = []
        for _hop in range(self._budget.max_redirects + 1):
            try:
                validate_fetch_target(
                    current_url, self._resolver, allowed_schemes=self._allowed_schemes
                )
            except UnsafeUrlError:
                raise _FetchRejected(FetchFailureReason.SSRF_REJECTED) from None

            headers = {
                "User-Agent": self._budget.user_agent,
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
                "Accept-Encoding": "gzip, deflate",
            }
            with httpx.Client(
                timeout=self._budget.fetch_timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                with client.stream("GET", current_url, headers=headers) as response:
                    status = response.status_code
                    if status in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise _FetchRejected(FetchFailureReason.HTTP_ERROR)
                        chain.append(current_url)
                        if len(chain) > self._budget.max_redirects:
                            raise _FetchRejected(FetchFailureReason.TOO_MANY_REDIRECTS)
                        current_url = urljoin(current_url, location)
                        continue
                    if status != 200:
                        raise _FetchRejected(FetchFailureReason.HTTP_ERROR)

                    content_type = _media_type(response.headers.get("content-type", ""))
                    if content_type not in self._budget.allowed_content_types:
                        raise _FetchRejected(FetchFailureReason.UNSUPPORTED_CONTENT_TYPE)

                    # Bounded streaming read: counts decompressed bytes, so a
                    # tiny gzip expanding to gigabytes still trips the limit.
                    chunks: list[bytes] = []
                    total = 0
                    limit = self._budget.max_response_bytes
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > limit:
                            raise _FetchRejected(FetchFailureReason.OVERSIZE)
                        chunks.append(chunk)
                    raw = b"".join(chunks)

            charset = _charset_of(response.headers.get("content-type", ""))
            text = raw.decode(charset or "utf-8", errors="replace")
            return FetchOutcome(
                page=FetchedPage(
                    final_url=current_url,
                    content_type=content_type,
                    text=text,
                    redirect_chain=tuple(chain),
                )
            )
        raise _FetchRejected(FetchFailureReason.TOO_MANY_REDIRECTS)


class _FetchRejected(Exception):
    """Internal control flow carrying a safe failure reason."""

    def __init__(self, reason: FetchFailureReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


def _media_type(content_type: str) -> str:
    return content_type.split(";")[0].strip().lower()


def _charset_of(content_type: str) -> str | None:
    for part in content_type.split(";")[1:]:
        key, _, value = part.partition("=")
        if key.strip().lower() == "charset":
            return value.strip().strip('"') or None
    return None
