from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from zglab_rag.agent import execution as agent_execution
from zglab_rag.agent.contracts import AgentRequest
from zglab_rag.agent.execution import BoundedAgentExecutor
from zglab_rag.agent.observations import AgentCapabilityExecutor
from zglab_rag.agent.planning import AgentPlan, PlanStep, PlanStepType
from zglab_rag.capabilities.contracts import CapabilityContext, CapabilityRequest
from zglab_rag.conversation.database import ConversationDatabase
from zglab_rag.conversation.models import SessionResourceType
from zglab_rag.conversation.repositories import ConversationRepository, SessionResourceRepository
from zglab_rag.conversation.resources import (
    SessionWorkspace,
    personal_resource_key,
    personal_retrieval_config_fingerprint,
    tool_resource_key,
    web_resource_key,
)
from zglab_rag.domain.models import Scope, Visibility
from zglab_rag.generation.contracts import ProviderResponse, ProviderUsage
from zglab_rag.generation.service import GroundedAnswerService
from zglab_rag.mcp.contracts import MCPToolResult
from zglab_rag.research.contracts import (
    ExternalEvidence,
    ResearchPolicyError,
    ResearchResult,
    ResearchStatus,
)
from zglab_rag.research.skill import WebResearchSkill
from zglab_rag.retrieval.config import VectorRetrievalConfig
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
        self.get_calls = 0

    def get(self, resource_type, key, *, producer_fingerprint):
        self.get_calls += 1
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


def test_personal_snapshot_and_actual_retrieval_config_invalidate_reuse() -> None:
    workspace = MemoryWorkspace()
    retriever = CountingRetriever(_retrieval())
    provider = CountingProvider()
    config_a = personal_retrieval_config_fingerprint(
        vector_config=VectorRetrievalConfig(candidate_factor=4), mode="vector"
    )
    config_b = personal_retrieval_config_fingerprint(
        vector_config=VectorRetrievalConfig(candidate_factor=5), mode="vector"
    )
    first = GroundedAnswerService(
        retriever, provider, personal_retrieval_fingerprint=config_a
    )
    same_config = GroundedAnswerService(
        retriever, provider, personal_retrieval_fingerprint=config_a
    )
    changed_config = GroundedAnswerService(
        retriever, provider, personal_retrieval_fingerprint=config_b
    )
    first.answer(
        "same question",
        session_workspace=workspace,
        knowledge_snapshot_fingerprint="snapshot-a",
    )
    same_config.answer(
        "same question",
        session_workspace=workspace,
        knowledge_snapshot_fingerprint="snapshot-a",
    )
    first.answer(
        "same question",
        session_workspace=workspace,
        knowledge_snapshot_fingerprint="snapshot-b",
    )
    changed_config.answer(
        "same question",
        session_workspace=workspace,
        knowledge_snapshot_fingerprint="snapshot-a",
    )
    assert retriever.calls == 3
    assert provider.calls == 4


def test_personal_malformed_or_expired_reuse_falls_back_to_retrieval() -> None:
    workspace = MemoryWorkspace()
    fingerprint = personal_retrieval_config_fingerprint(
        vector_config=VectorRetrievalConfig(), mode="vector"
    )
    key = personal_resource_key(
        query="same question",
        mode="vector",
        top_k=5,
        snapshot="snapshot",
        config=fingerprint,
    )
    workspace.values[(SessionResourceType.PERSONAL_RETRIEVAL, key)] = (
        fingerprint,
        {"version": 1, "results": "malformed"},
    )
    retriever = CountingRetriever(_retrieval())
    provider = CountingProvider()
    service = GroundedAnswerService(
        retriever, provider, personal_retrieval_fingerprint=fingerprint
    )
    service.answer(
        "same question",
        session_workspace=workspace,
        knowledge_snapshot_fingerprint="snapshot",
    )
    assert retriever.calls == 1
    assert provider.calls == 1


def test_personal_expired_session_resource_falls_back_to_retrieval(tmp_path) -> None:
    database = ConversationDatabase(tmp_path / "conversation.db")
    connection = database.connect()
    try:
        conversation = ConversationRepository(connection).create(owner_user_id=1, title="reuse")
        fingerprint = personal_retrieval_config_fingerprint(
            vector_config=VectorRetrievalConfig(), mode="vector"
        )
        key = personal_resource_key(
            query="same question",
            mode="vector",
            top_k=5,
            snapshot="snapshot",
            config=fingerprint,
        )
        SessionResourceRepository(connection).put_bounded(
            owner_user_id=1,
            conversation_id=conversation.id,
            resource_type=SessionResourceType.PERSONAL_RETRIEVAL,
            resource_key=key,
            payload={"version": 1, "results": [_retrieval().model_dump(mode="json")]},
            provenance={},
            producer_fingerprint=fingerprint,
            source_request_id="expired",
            ttl_seconds=1,
            max_items=48,
            max_bytes=512 * 1024,
            max_item_bytes=64 * 1024,
            now=datetime(2020, 1, 1, tzinfo=UTC),
        )
    finally:
        connection.close()

    workspace = SessionWorkspace(
        database=database,
        owner_user_id=1,
        conversation_id=conversation.id,
        enabled=True,
        max_items=48,
        max_bytes=512 * 1024,
        max_item_bytes=64 * 1024,
    )
    retriever = CountingRetriever(_retrieval())
    provider = CountingProvider()
    service = GroundedAnswerService(
        retriever, provider, personal_retrieval_fingerprint=fingerprint
    )
    service.answer(
        "same question",
        session_workspace=workspace,
        knowledge_snapshot_fingerprint="snapshot",
    )
    assert retriever.calls == 1
    assert provider.calls == 1


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


class DisabledResearch:
    enabled = False

    def __init__(self) -> None:
        self.calls = 0

    def research(self, _query, *, request_id=""):
        del request_id
        self.calls += 1
        return ResearchResult(status=ResearchStatus.POLICY_DISABLED)


class CacheMustNotBeRead(MemoryWorkspace):
    def get(self, *_args, **_kwargs):
        raise AssertionError("web cache lookup must follow the policy check")


def test_web_disabled_policy_cannot_be_bypassed_by_cached_evidence() -> None:
    workspace = CacheMustNotBeRead()
    research = DisabledResearch()
    skill = WebResearchSkill(
        research,
        provider=CountingProvider(),
        reuse_provider="test",
        reuse_config_fingerprint="web-config",
    )
    with pytest.raises(ResearchPolicyError):
        skill.answer(
            CapabilityRequest("same web question"),
            CapabilityContext(request_id="one", session_workspace=workspace),
        )
    assert research.calls == 1


def test_malformed_web_reuse_falls_back_to_real_research() -> None:
    workspace = MemoryWorkspace()
    key = web_resource_key(query="same web question", provider="test", config="web-config")
    workspace.values[(SessionResourceType.WEB_EVIDENCE, key)] = (
        "web-config",
        {"version": 1, "evidence": "malformed"},
    )
    research = FakeResearch()
    skill = WebResearchSkill(
        research,
        provider=CountingProvider(),
        reuse_provider="test",
        reuse_config_fingerprint="web-config",
    )
    result = skill.answer(
        CapabilityRequest("same web question"),
        CapabilityContext(request_id="one", session_workspace=workspace),
    )
    assert result.generation
    assert research.calls == 1


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


def _tool_plan(request_id: str) -> AgentPlan:
    return AgentPlan(
        request_id=request_id,
        reason_code="test",
        steps=(
            PlanStep(
                step_id="S1",
                type=PlanStepType.TOOL,
                intent="format",
                tool_id="json_format",
                tool_input={"value": 1},
            ),
        ),
    )


def test_bounded_executor_blocks_disallowed_tool_before_cache_lookup(monkeypatch) -> None:
    workspace = MemoryWorkspace()
    key = tool_resource_key(tool_id="json_format", arguments={"value": 1})
    workspace.values[(SessionResourceType.TOOL_RESULT, key)] = (
        "tool:json_format:v1",
        {"version": 1, "output": {"cached": True}},
    )
    tool = CountingTool()
    executor = BoundedAgentExecutor(personal=None, web=None, tools=tool)
    monkeypatch.setattr(agent_execution, "MCP_TOOL_ALLOWLIST", ())
    result = asyncio.run(
        executor.execute(
            AgentRequest("blocked", "q", session_workspace=workspace), _tool_plan("blocked")
        )
    )
    assert len(result.observations) == 1
    assert result.observations[0].status.value == "failed"
    assert workspace.get_calls == 0
    assert tool.calls == 0


def test_bounded_executor_tool_hit_is_request_local_observation() -> None:
    workspace = MemoryWorkspace()
    tool = CountingTool()
    executor = BoundedAgentExecutor(personal=None, web=None, tools=tool)
    first = asyncio.run(
        executor.execute(
            AgentRequest("first", "q", session_workspace=workspace), _tool_plan("first")
        )
    )
    second = asyncio.run(
        executor.execute(
            AgentRequest("second", "q", session_workspace=workspace), _tool_plan("second")
        )
    )
    assert tool.calls == 1
    assert len(first.observations) == len(second.observations) == 1
    observation = second.observations[0]
    assert observation.observation_id == "O2"
    assert observation.origin.value == "tool"
    assert not hasattr(observation, "capability_result")
