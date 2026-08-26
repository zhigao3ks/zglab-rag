"""Web Research contracts (Phase 12B).

Defines the internal, provider-neutral models of the research pipeline:

    SearchProvider -> SearchResult[] -> candidates -> safe fetch
        -> extraction -> normalization -> ExternalEvidence[]

Hard semantics frozen here:
- Web content is UNTRUSTED EXTERNAL DATA. "Ignore previous instructions",
  "call tools" or "reveal the system prompt" text inside a page is only
  evidence text; 12C must keep a data boundary when it reaches an LLM.
- ExternalEvidence.url must originate from a real SearchProvider result or
  its validated redirect chain — never from an LLM, extractor or user text.
- Web pages are not Markdown knowledge chunks: no chunk_id / file_path /
  heading_path. The E1/E2 citation namespace stays untouched until 12C.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

from zglab_rag.capabilities.contracts import EvidenceOrigin

WEB_RESEARCH_CAPABILITY_ID = "web_research"


class SearchResult(BaseModel):
    """One provider-neutral search hit.

    Provider-specific JSON must never leak beyond the provider adapter.
    """

    title: str = ""
    url: str
    snippet: str = ""
    rank: int = Field(ge=1)
    provider: str
    published_at: datetime | None = None


class SearchProvider(Protocol):
    """Replaceable search backend.

    Exactly one search() call is allowed per research request (cost
    boundary); the service never loops over queries.
    """

    name: str

    def search(self, query: str, *, limit: int) -> list[SearchResult]: ...


class FetchFailureReason(StrEnum):
    """Candidate-level outcomes; safe to surface internally, never raw errors."""

    SSRF_REJECTED = "ssrf_rejected"
    UNSAFE_URL = "unsafe_url"
    TOO_MANY_REDIRECTS = "too_many_redirects"
    UNSUPPORTED_CONTENT_TYPE = "unsupported_content_type"
    HTTP_ERROR = "http_error"
    TIMEOUT = "timeout"
    OVERSIZE = "oversize"
    EMPTY_OR_LOW_QUALITY = "empty_or_low_quality"
    FETCH_ERROR = "fetch_error"


class ExternalEvidence(BaseModel):
    """One fetched, extracted and normalized web evidence item.

    origin is always WEB and trust is always untrusted in Phase 12B; the
    evidence_id namespace is W1/W2/... (request-stable, decoupled from the
    future display citation id, which 12C will unify with E1/E2).
    """

    evidence_id: str = Field(pattern=r"^W[1-9][0-9]*$")
    origin: EvidenceOrigin = EvidenceOrigin.WEB
    trust: str = "untrusted"
    title: str = ""
    url: str
    canonical_url: str
    domain: str
    content: str
    snippet: str = ""
    search_rank: int = Field(ge=1)
    retrieved_at: datetime
    # Provenance (hard requirement): the search URL that produced this page
    # and every validated redirect hop up to the final URL.
    search_result_url: str
    redirect_chain: tuple[str, ...] = ()


class ResearchStatus(StrEnum):
    """Minimal, explainable outcome classes.

    NO_RESULTS / NO_USABLE_EVIDENCE are business outcomes (a future policy
    may act on them); PROVIDER_UNAVAILABLE / TIMEOUT / TECHNICAL_FAILURE are
    infrastructure problems and must never be treated as "knowledge absent";
    POLICY_DISABLED means the kill switch refused the work before any cost.
    """

    SUCCESS = "success"
    NO_RESULTS = "no_results"
    NO_USABLE_EVIDENCE = "no_usable_evidence"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TIMEOUT = "timeout"
    TECHNICAL_FAILURE = "technical_failure"
    POLICY_DISABLED = "policy_disabled"


class CandidateFailure(BaseModel):
    """Safe per-candidate failure summary (no keys, IPs or stack traces)."""

    url: str
    reason: FetchFailureReason


class ResearchResult(BaseModel):
    """Full outcome of one bounded research request."""

    status: ResearchStatus
    evidence: list[ExternalEvidence] = Field(default_factory=list)
    search_result_count: int = 0
    attempted_count: int = 0
    fetched_count: int = 0
    failures: list[CandidateFailure] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ResearchBudget:
    """Hard bounds for one research request (2 vCPU / 2 GiB conservative).

    max_search_calls is structurally 1 in Phase 12B: the service calls the
    provider exactly once.
    """

    max_search_results: int = 6
    max_fetch_candidates: int = 4
    max_candidates_per_domain: int = 2
    max_redirects: int = 3
    fetch_timeout_seconds: float = 8.0
    overall_timeout_seconds: float = 30.0
    max_response_bytes: int = 1_572_864  # 1.5 MiB (decompressed bytes read)
    max_extracted_chars: int = 8_000
    min_evidence_chars: int = 200
    allowed_content_types: tuple[str, ...] = (
        "text/html",
        "text/plain",
        "application/xhtml+xml",
    )
    user_agent: str = "zglab-rag-research/0.1 (+https://ask.zglab.fun)"
    extra: dict[str, str] = field(default_factory=dict)


class SearchProviderError(Exception):
    """The search backend failed (network, HTTP error, bad payload...)."""


class SearchProviderUnavailableError(SearchProviderError):
    """The search backend is unreachable / refused the request."""


class ResearchPolicyError(Exception):
    """Research refused by policy before spending anything (kill switch)."""
