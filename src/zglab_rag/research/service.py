"""ResearchService: the bounded web research pipeline (Phase 12B).

    query -> ONE search call -> deterministic candidates -> safe fetch
        -> deterministic extraction -> normalization -> ExternalEvidence[]

Partial success is a first-class outcome: one good page among failing
candidates still yields SUCCESS. Only zero usable evidence becomes
NO_USABLE_EVIDENCE, and infrastructure problems map to their own statuses
(a search outage must never be misread as "the web has no answer").

The service is fail-closed on the kill switch and never writes to
knowledge.db, never calls an LLM and never exposes an HTTP endpoint.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

from zglab_rag.research.contracts import (
    CandidateFailure,
    ExternalEvidence,
    FetchFailureReason,
    ResearchBudget,
    ResearchResult,
    ResearchStatus,
    SearchProvider,
    SearchProviderError,
    SearchProviderUnavailableError,
)
from zglab_rag.research.errors import UnsafeUrlError
from zglab_rag.research.extract import extract_html, extract_plain_text
from zglab_rag.research.fetch import SafeFetcher
from zglab_rag.research.search import select_candidates
from zglab_rag.research.url_safety import canonicalize_url

logger = logging.getLogger(__name__)

_WHITELISTED_TEXT = "text/plain"


class ResearchService:
    """Sync, bounded, deterministic research pipeline."""

    def __init__(
        self,
        provider: SearchProvider,
        budget: ResearchBudget | None = None,
        *,
        enabled: bool = False,
        fetcher: SafeFetcher | None = None,
        clock=time.monotonic,
    ) -> None:
        self._provider = provider
        self._budget = budget or ResearchBudget()
        self._enabled = enabled
        self._fetcher = fetcher or SafeFetcher(self._budget)
        self._clock = clock

    def research(self, query: str, *, request_id: str = "") -> ResearchResult:
        started = self._clock()
        # Kill switch: fail closed before spending any search/fetch cost.
        if not self._enabled:
            logger.info("research request_id=%s status=policy_disabled", request_id)
            return ResearchResult(status=ResearchStatus.POLICY_DISABLED)

        normalized = " ".join(query.split())
        if not normalized:
            return ResearchResult(status=ResearchStatus.NO_RESULTS)

        deadline = started + self._budget.overall_timeout_seconds

        # Exactly one search call per research request (cost boundary).
        try:
            results = self._provider.search(
                normalized, limit=self._budget.max_search_results
            )
        except SearchProviderUnavailableError:
            logger.warning("research request_id=%s status=provider_unavailable", request_id)
            return ResearchResult(status=ResearchStatus.PROVIDER_UNAVAILABLE)
        except SearchProviderError:
            logger.warning("research request_id=%s status=technical_failure", request_id)
            return ResearchResult(status=ResearchStatus.TECHNICAL_FAILURE)

        if not results:
            return ResearchResult(
                status=ResearchStatus.NO_RESULTS, search_result_count=0
            )

        candidates, _rejected = select_candidates(results, self._budget)
        evidence: list[ExternalEvidence] = []
        failures: list[CandidateFailure] = []
        timed_out = False
        seen_canonical: set[str] = set()
        seen_content: set[str] = set()
        fetched_count = 0

        for candidate in candidates:
            if self._clock() > deadline:
                timed_out = True
                break
            outcome = self._fetcher.fetch(candidate.result.url)
            if not outcome.ok or outcome.page is None:
                failures.append(
                    CandidateFailure(
                        url=candidate.result.url,
                        reason=outcome.reason or FetchFailureReason.FETCH_ERROR,
                    )
                )
                continue
            page = outcome.page
            fetched_count += 1

            extracted = (
                extract_plain_text(page.text, max_chars=self._budget.max_extracted_chars)
                if page.content_type == _WHITELISTED_TEXT
                else extract_html(page.text, max_chars=self._budget.max_extracted_chars)
            )
            if len(extracted.text) < self._budget.min_evidence_chars:
                failures.append(
                    CandidateFailure(
                        url=candidate.result.url,
                        reason=FetchFailureReason.EMPTY_OR_LOW_QUALITY,
                    )
                )
                continue

            # Dedupe: canonical final URL + content hash (covers pages that
            # different search URLs redirect to).
            try:
                final_canonical = canonicalize_url(page.final_url)
            except UnsafeUrlError:
                final_canonical = candidate.canonical_url
            content_hash = hashlib.sha256(extracted.text.encode()).hexdigest()
            if final_canonical in seen_canonical or content_hash in seen_content:
                continue
            seen_canonical.add(final_canonical)
            seen_content.add(content_hash)

            evidence.append(
                ExternalEvidence(
                    evidence_id=f"W{len(evidence) + 1}",
                    title=extracted.title or candidate.result.title,
                    url=page.final_url,
                    canonical_url=final_canonical,
                    domain=urlsplit(page.final_url).hostname or "",
                    content=extracted.text,
                    snippet=candidate.result.snippet[:300],
                    search_rank=candidate.result.rank,
                    retrieved_at=datetime.now(UTC),
                    search_result_url=candidate.result.url,
                    redirect_chain=page.redirect_chain,
                )
            )

        if evidence:
            status = ResearchStatus.SUCCESS
        elif timed_out:
            status = ResearchStatus.TIMEOUT
        else:
            status = ResearchStatus.NO_USABLE_EVIDENCE

        elapsed_ms = (self._clock() - started) * 1000
        # Safe logging: counts / domains / status only. Never the query,
        # page content, keys or internal exception details.
        domains = sorted({item.domain for item in evidence})
        logger.info(
            "research request_id=%s provider=%s search_results=%d candidates=%d "
            "fetched=%d evidence=%d status=%s domains=%s elapsed_ms=%.1f",
            request_id,
            self._provider.name,
            len(results),
            len(candidates),
            fetched_count,
            len(evidence),
            status.value,
            ",".join(domains),
            elapsed_ms,
        )
        return ResearchResult(
            status=status,
            evidence=evidence,
            search_result_count=len(results),
            attempted_count=len(candidates),
            fetched_count=fetched_count,
            failures=failures,
        )
