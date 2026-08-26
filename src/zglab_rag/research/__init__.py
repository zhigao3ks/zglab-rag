"""Web Research Core (Phase 12B).

An independent, bounded, deterministic pipeline that turns a question
into untrusted ExternalEvidence[]:

    SearchProvider -> candidates -> SafeFetcher -> extraction
        -> normalization -> ExternalEvidence[]

It is NOT wired to /api/v2/ask, does not generate answers, and does not
write to knowledge.db. Integration with grounded generation is Phase 12C.
"""

from zglab_rag.research.contracts import (
    WEB_RESEARCH_CAPABILITY_ID,
    CandidateFailure,
    ExternalEvidence,
    FetchFailureReason,
    ResearchBudget,
    ResearchPolicyError,
    ResearchResult,
    ResearchStatus,
    SearchProviderError,
    SearchProviderUnavailableError,
    SearchResult,
)
from zglab_rag.research.errors import FetchError, ResearchError, UnsafeUrlError
from zglab_rag.research.fetch import FetchedPage, FetchOutcome, SafeFetcher
from zglab_rag.research.search import (
    Candidate,
    FakeSearchProvider,
    TavilySearchProvider,
    select_candidates,
)
from zglab_rag.research.service import ResearchService
from zglab_rag.research.skill import WebResearchSkill, build_research_service

__all__ = [
    "WEB_RESEARCH_CAPABILITY_ID",
    "Candidate",
    "CandidateFailure",
    "ExternalEvidence",
    "FetchError",
    "FetchFailureReason",
    "FetchOutcome",
    "FetchedPage",
    "FakeSearchProvider",
    "ResearchBudget",
    "ResearchError",
    "ResearchPolicyError",
    "ResearchResult",
    "ResearchService",
    "ResearchStatus",
    "SafeFetcher",
    "SearchProviderError",
    "SearchProviderUnavailableError",
    "SearchResult",
    "TavilySearchProvider",
    "UnsafeUrlError",
    "WebResearchSkill",
    "build_research_service",
    "select_candidates",
]
