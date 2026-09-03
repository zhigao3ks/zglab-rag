from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from zglab_rag.agent.contracts import AgentRequest
from zglab_rag.agent.observations import AgentCapabilityExecutor
from zglab_rag.capabilities.contracts import CapabilityContext, CapabilityRequest
from zglab_rag.conversation.models import SessionResourceType
from zglab_rag.domain.models import Scope, Visibility
from zglab_rag.generation.contracts import ProviderResponse, ProviderUsage
from zglab_rag.generation.service import GroundedAnswerService
from zglab_rag.mcp.contracts import MCPToolResult
from zglab_rag.research.contracts import ExternalEvidence, ResearchResult, ResearchStatus
from zglab_rag.research.skill import WebResearchSkill
from zglab_rag.retrieval.contracts import (
    RetrievalDiagnostics,
    RetrievalFilter,
    RetrievalResponse,
    RetrievalResult,
)


class MemoryWorkspace:
    personal_ttl_seconds = 60
    web_ttl_seconds = 60
    tool_ttl_seconds = 60

    def __init__(self) -> None:
        self.values: dict[tuple[SessionResourceType, str], tuple[str, dict]] = {}

    def get(self, resource_type, key, *, producer_fingerprint):
        value = self.values.get((resource_type, key))
        return value[1] if value and value[0] == producer_fingerprint else None

    def put(self, resource_type, key, *, payload, provenance, producer_fingerprint, **_kwargs):
        self.values[(resource_type, key)] = (producer_fingerprint, payload)


class CountingRetriever:
    def __init__(self, result: RetrievalResult) -> None:
        self.result = result
        self.calls = 0

    def retrieve(self, query):
        self.calls += 1
        return RetrievalResponse(
            results=[self.result],
            diagnostics=RetrievalDiagnostics(
                query_embedding_latency_ms=1,
                vector_search_latency_ms=1,
                total_retrieval_latency_ms=1,
                candidate_count=1,
                filtered_count=0,
                returned_count=1,
                top_k=query.top_k or 5,
                filters=RetrievalFilter(),
            ),
        )


class CountingProvider:
    name = "test"
    model = "test"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, _request):
        self.calls += 1
        return ProviderResponse(
            provider=self.name,
            model=self.model,
            text=json.dumps(
                {
                    "answer": "hidden",
                    "claims": [{"text": "grounded", "citations": ["E1"]}],
                    "citations": [],
                    "insufficient_evidence": False,
                }
            ),
            latency_ms=1,
            usage=ProviderUsage(),
        )


def _retrieval() -> RetrievalResult:
    return RetrievalResult(
        chunk_id="chunk",
        document_id="doc",
        source_id="source",
        source_path="source.md",
        scope=Scope.KNOWLEDGE,
        title="title",
        section_path=["section"],
        content="evidence",
        visibility=Visibility.PUBLIC,
        revision="r1",
        rank=1,
        score=1.0,
    )


def test_personal_exact_hit_skips_retrieval_but_regenerates() -> None:
    workspace = MemoryWorkspace()
    retriever = CountingRetriever(_retrieval())
    provider = CountingProvider()
    service = GroundedAnswerService(retriever, provider)
    for request_id in ("one", "two"):
        result = service.answer(
            "same question",
            session_workspace=workspace,
            knowledge_snapshot_fingerprint="snapshot",
            request_id=request_id,
        )
        assert result.answer.claims
    assert retriever.calls == 1
    assert provider.calls == 2


class FakeResearch:
    enabled = True

    def __init__(self) -> None:
        self.calls = 0

    def research(self, _query, *, request_id=""):
        del request_id
        self.calls += 1
        return ResearchResult(
            status=ResearchStatus.SUCCESS,
            evidence=[
                ExternalEvidence(
                    evidence_id="W1",
                    title="web",
                    url="https://example.test/a",
                    canonical_url="https://example.test/a",
                    domain="example.test",
                    content="web evidence",
                    search_rank=1,
                    retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
                    search_result_url="https://example.test/a",
                    redirect_chain=("https://example.test/a",),
                )
            ],
        )


def test_web_exact_hit_rehydrates_new_evidence_for_current_generation() -> None:
    workspace = MemoryWorkspace()
    research = FakeResearch()
    provider = CountingProvider()
    skill = WebResearchSkill(
        research,
        provider=provider,
        reuse_provider="test",
        reuse_config_fingerprint="web-config",
        reuse_ttl_seconds=60,
    )
    for request_id in ("one", "two"):
        result = skill.answer(
            CapabilityRequest("same web question"),
            CapabilityContext(request_id=request_id, session_workspace=workspace),
        )
        assert result.generation and result.generation.answer.sources[0].evidence_id == "E1"
    assert research.calls == 1
    assert provider.calls == 2


class CountingTool:
    def __init__(self) -> None:
        self.calls = 0

    async def call_tool(self, tool_id, arguments):
        self.calls += 1
        return MCPToolResult("success", tool_id, output={"value": arguments["value"]})


def test_tool_canonical_argument_reuse_keeps_new_tool_observation() -> None:
    workspace = MemoryWorkspace()
    adapter = AgentCapabilityExecutor()
    tool = CountingTool()
    first = asyncio.run(
        adapter.invoke_tool(
            AgentRequest("one", "q", session_workspace=workspace),
            tool,
            "json_format",
            {"value": 1, "x": 2},
        )
    )
    second = asyncio.run(
        adapter.invoke_tool(
            AgentRequest("two", "q", session_workspace=workspace),
            tool,
            "json_format",
            {"x": 2, "value": 1},
        )
    )
    assert tool.calls == 1
    assert first.observation_id == "O1" and second.observation_id == "O2"
    assert second.origin.value == "tool" and second.structured_result == {"value": 1}
