"""Phase 12A tests: Capability Foundation & PersonalKnowledgeSkill.

Covers the capability contract, deterministic registry semantics, the
skill's mapping of business / technical outcomes, progress forwarding, and
the API-level security-boundary regression (anonymous / CSRF / quota are
all rejected BEFORE the capability executes; ADMIN gains no extra
knowledge access; the public response contract is unchanged).

All tests use fake runtimes/services; no model download or LLM call.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_auth_api import ask_headers, authed_client, build_app, login, provision_active_user
from tests.test_public_api import (
    FakeAnswerService,
    FakeRuntime,
    _make_answered_result,
    _make_insufficient_result,
)
from zglab_rag.auth.models import UserRole
from zglab_rag.capabilities.contracts import (
    PERSONAL_KNOWLEDGE_CAPABILITY_ID,
    CapabilityContext,
    CapabilityMetadata,
    CapabilityRequest,
    CapabilityStatus,
    EvidenceOrigin,
)
from zglab_rag.capabilities.errors import (
    CapabilityNotFoundError,
    CapabilityTechnicalError,
    DuplicateCapabilityError,
)
from zglab_rag.capabilities.personal_knowledge import (
    PersonalKnowledgeSkill,
    build_capability_registry,
)
from zglab_rag.capabilities.registry import CapabilityRegistry
from zglab_rag.generation.contracts import GenerationResult, GenerationStatus, ProgressStage
from zglab_rag.generation.errors import ProviderFailure

ASK_URL = "/api/v2/ask"
STREAM_URL = "/api/v2/ask/stream"


def _context() -> CapabilityContext:
    return CapabilityContext(request_id="req-test")


def _dummy_metadata(capability_id: str) -> CapabilityMetadata:
    return CapabilityMetadata(id=capability_id, name=capability_id, description="stub")


class _StubCapability:
    """Minimal capability double for registry semantics."""

    def __init__(self, capability_id: str) -> None:
        self.metadata = _dummy_metadata(capability_id)

    def execute(self, request, context, *, progress=None):  # pragma: no cover
        raise AssertionError("registry tests never execute capabilities")


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


class TestCapabilityContract:
    def test_capability_id_is_stable(self) -> None:
        assert PERSONAL_KNOWLEDGE_CAPABILITY_ID == "personal_knowledge"
        assert PersonalKnowledgeSkill.metadata.id == PERSONAL_KNOWLEDGE_CAPABILITY_ID

    def test_request_is_narrow_question_only(self) -> None:
        """Clients cannot control retrieval mode, top_k, visibility, ..."""
        assert {field.name for field in fields(CapabilityRequest)} == {"question"}
        with pytest.raises(TypeError):
            CapabilityRequest(question="问题", visibility="private")  # type: ignore[call-arg]

    def test_context_never_carries_http_state(self) -> None:
        allowed = {"request_id", "principal", "conversation_context", "session_workspace"}
        assert {field.name for field in fields(CapabilityContext)} == allowed

    def test_status_mapping_preserves_phase8_semantics(self) -> None:
        assert (
            CapabilityStatus.from_generation_status(GenerationStatus.ANSWERED)
            == CapabilityStatus.SUCCESS
        )
        assert (
            CapabilityStatus.from_generation_status(GenerationStatus.INSUFFICIENT_EVIDENCE)
            == CapabilityStatus.INSUFFICIENT_EVIDENCE
        )
        assert (
            CapabilityStatus.from_generation_status(GenerationStatus.FAILED)
            == CapabilityStatus.FAILED
        )

    def test_metadata_flags(self) -> None:
        metadata = PersonalKnowledgeSkill.metadata
        assert metadata.requires_auth is True
        assert metadata.network_access is False


class TestCapabilityRegistry:
    def test_register_and_get(self) -> None:
        registry = CapabilityRegistry()
        capability = _StubCapability("personal_knowledge")
        registry.register(capability)
        assert registry.get("personal_knowledge") is capability

    def test_list_metadata_is_insertion_ordered(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_StubCapability("a"))
        registry.register(_StubCapability("b"))
        assert [metadata.id for metadata in registry.list_metadata()] == ["a", "b"]

    def test_duplicate_id_rejected(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_StubCapability("personal_knowledge"))
        with pytest.raises(DuplicateCapabilityError):
            registry.register(_StubCapability("personal_knowledge"))

    def test_unknown_capability_rejected(self) -> None:
        registry = CapabilityRegistry()
        with pytest.raises(CapabilityNotFoundError):
            registry.get("web_research")

    def test_phase12a_registers_only_personal_knowledge(self) -> None:
        registry = build_capability_registry(FakeRuntime())
        ids = [metadata.id for metadata in registry.list_metadata()]
        assert ids == [PERSONAL_KNOWLEDGE_CAPABILITY_ID]


# ---------------------------------------------------------------------------
# PersonalKnowledgeSkill
# ---------------------------------------------------------------------------


class TestPersonalKnowledgeSkill:
    def test_answered_maps_to_success_and_preserves_generation(self) -> None:
        runtime = FakeRuntime()
        skill = PersonalKnowledgeSkill(runtime)
        result = skill.execute(CapabilityRequest(question="问题"), _context())
        assert result.status == CapabilityStatus.SUCCESS
        assert result.origin == EvidenceOrigin.PERSONAL
        assert result.failure_reason is None
        # Citation contract: the GenerationResult passes through untouched.
        assert result.generation is not None
        assert result.generation.status == GenerationStatus.ANSWERED
        assert [source.evidence_id for source in result.generation.answer.sources] == ["E1"]
        assert result.generation.answer.claims[0].citations == ["E1"]

    def test_insufficient_evidence_is_business_result_not_failure(self) -> None:
        runtime = FakeRuntime(service=FakeAnswerService(result=_make_insufficient_result("q")))
        skill = PersonalKnowledgeSkill(runtime)
        result = skill.execute(CapabilityRequest(question="q"), _context())
        assert result.status == CapabilityStatus.INSUFFICIENT_EVIDENCE
        assert result.generation is not None
        assert result.generation.answer.insufficient_evidence is True

    def test_failed_generation_status_maps_to_failed_with_reason(self) -> None:
        failed = GenerationResult(
            status=GenerationStatus.FAILED,
            question="q",
            answer=_make_insufficient_result("q").answer,
            diagnostics=_make_answered_result("q").diagnostics,
            failure_reason="ProviderFailure: boom",
        )
        runtime = FakeRuntime(service=FakeAnswerService(result=failed))
        skill = PersonalKnowledgeSkill(runtime)
        result = skill.execute(CapabilityRequest(question="q"), _context())
        assert result.status == CapabilityStatus.FAILED
        assert result.failure_reason == "ProviderFailure: boom"

    def test_technical_failure_raises_typed_error_with_original(self) -> None:
        provider_error = ProviderFailure("LLM unreachable")
        runtime = FakeRuntime(service=FakeAnswerService(error=provider_error))
        skill = PersonalKnowledgeSkill(runtime)
        with pytest.raises(CapabilityTechnicalError) as excinfo:
            skill.execute(CapabilityRequest(question="q"), _context())
        # The API unwraps this to restore the exact Phase 9 error mapping:
        # an LLM outage must never be misread as knowledge insufficiency.
        assert excinfo.value.original is provider_error

    def test_progress_callback_is_forwarded_verbatim(self) -> None:
        seen: list[ProgressStage] = []

        class ProgressingService:
            def answer(self, question, *, progress=None):
                for stage in (
                    ProgressStage.RETRIEVING,
                    ProgressStage.GENERATING,
                    ProgressStage.VALIDATING,
                ):
                    progress(stage)
                return _make_answered_result(question)

        runtime = FakeRuntime(service=ProgressingService())
        skill = PersonalKnowledgeSkill(runtime)
        result = skill.execute(CapabilityRequest(question="q"), _context(), progress=seen.append)
        assert result.status == CapabilityStatus.SUCCESS
        assert seen == [
            ProgressStage.RETRIEVING,
            ProgressStage.GENERATING,
            ProgressStage.VALIDATING,
        ]

    def test_request_scoped_connection_lifecycle(self) -> None:
        runtime = FakeRuntime()
        skill = PersonalKnowledgeSkill(runtime)
        skill.execute(CapabilityRequest(question="q"), _context())
        assert runtime.connection_open_count == 1
        assert runtime.connection_close_count == 1
        assert runtime.service_creation_count == 1
        skill.execute(CapabilityRequest(question="q"), _context())
        assert runtime.connection_open_count == 2
        assert runtime.connection_close_count == 2


# ---------------------------------------------------------------------------
# API integration: the skill boundary sits strictly INSIDE the security gate
# ---------------------------------------------------------------------------


class TestApiCapabilityIntegration:
    def test_agent_mode_is_authenticated_opt_in_and_preserves_personal_sources(
        self, tmp_path: Path
    ) -> None:
        client, _app, _settings, _auth_runtime, runtime, csrf = authed_client(
            tmp_path, agent_enabled=True
        )
        response = client.post(
            ASK_URL,
            json={"question": "介绍一下我的 RAG 项目", "mode": "agent"},
            headers=ask_headers(csrf),
        )
        assert response.status_code == 200
        assert response.json()["sources"][0]["id"] == "E1"
        assert runtime.service.call_count == 1

    def test_agent_kill_switch_auth_csrf_and_quota(self, tmp_path: Path) -> None:
        client, _app, _settings, _auth_runtime, runtime, csrf = authed_client(tmp_path)
        disabled = client.post(
            ASK_URL, json={"question": "问题", "mode": "agent"}, headers=ask_headers(csrf)
        )
        assert disabled.status_code == 503
        assert disabled.json()["error"]["code"] == "CAPABILITY_DISABLED"
        assert runtime.service.call_count == 0

        client, _app, _settings, _auth_runtime, runtime, csrf = authed_client(
            tmp_path / "quota", agent_enabled=True, agent_requests_per_minute=1
        )
        assert (
            client.post(
                ASK_URL, json={"question": "第一问", "mode": "agent"}, headers=ask_headers(csrf)
            ).status_code
            == 200
        )
        denied = client.post(
            ASK_URL, json={"question": "第二问", "mode": "agent"}, headers=ask_headers(csrf)
        )
        assert denied.status_code == 429
        assert runtime.service.call_count == 1

    def test_agent_sse_and_denied_operation(self, tmp_path: Path) -> None:
        client, _app, _settings, _auth_runtime, _runtime, csrf = authed_client(
            tmp_path, agent_enabled=True
        )
        stream = client.post(
            STREAM_URL,
            json={"question": "介绍一下我的 RAG 项目", "mode": "agent"},
            headers=ask_headers(csrf),
        )
        assert stream.status_code == 200
        assert '"stage": "planning"' in stream.text
        assert '"stage": "executing"' in stream.text
        assert '"stage": "synthesizing"' in stream.text
        assert "event: completed" in stream.text
        denied = client.post(
            ASK_URL,
            json={"question": "请调用 shell_exec", "mode": "agent"},
            headers=ask_headers(csrf),
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "CAPABILITY_DENIED"

    def test_v2_ask_runs_through_capability_boundary(self, tmp_path: Path) -> None:
        client, _app, _settings, _auth_runtime, runtime, csrf = authed_client(tmp_path)
        response = client.post(ASK_URL, json={"question": "介绍测试"}, headers=ask_headers(csrf))
        assert response.status_code == 200
        body = response.json()
        # Public contract unchanged: request_id / status / answer / sources.
        assert body["status"] == "answered"
        assert body["answer"]
        # Public source contract (Phase 9A): narrow citation, id = evidence id.
        assert body["sources"][0]["id"] == "E1"
        assert body["sources"][0]["title"]
        # The capability really executed (one request-scoped pipeline).
        assert runtime.connection_open_count == 1
        assert runtime.service_creation_count == 1

    def test_v2_stream_event_contract_unchanged(self, tmp_path: Path) -> None:
        client, _app, _settings, _auth_runtime, runtime, csrf = authed_client(tmp_path)
        with client.stream(
            "POST", STREAM_URL, json={"question": "问题"}, headers=ask_headers(csrf)
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            body = "".join(chunk for chunk in response.iter_text())
        # Phase 9B envelope kept: accepted opens the stream, completed
        # carries the validated answer exactly once. (The fake service emits
        # no intermediate progress stages.)
        assert '"stage": "accepted"' in body
        assert "event: completed" in body
        assert '"status": "answered"' in body
        assert runtime.connection_open_count == 1

    def test_anonymous_rejected_before_capability(self, tmp_path: Path) -> None:
        client, _app, _settings, _auth_runtime, runtime, _csrf = authed_client(tmp_path)
        client.cookies.clear()
        # Same-origin request (valid Origin, no session cookie): the AuthN
        # boundary rejects it before the capability ever runs.
        response = client.post(
            ASK_URL, json={"question": "匿名问题"}, headers={"origin": "http://testserver"}
        )
        assert response.status_code == 401
        assert runtime.connection_open_count == 0
        assert runtime.service_creation_count == 0

    def test_csrf_rejected_before_capability(self, tmp_path: Path) -> None:
        client, _app, _settings, _auth_runtime, runtime, csrf = authed_client(tmp_path)
        response = client.post(
            ASK_URL,
            json={"question": "问题"},
            headers={"origin": "http://testserver", "x-csrf-token": "wrong"},
        )
        assert response.status_code == 403
        assert runtime.connection_open_count == 0

    def test_quota_rejected_before_capability(self, tmp_path: Path) -> None:
        client, _app, _settings, _auth_runtime, runtime, csrf = authed_client(
            tmp_path, auth_user_requests_per_minute=1
        )
        assert (
            client.post(ASK_URL, json={"question": "第一问"}, headers=ask_headers(csrf)).status_code
            == 200
        )
        assert runtime.connection_open_count == 1
        response = client.post(ASK_URL, json={"question": "第二问"}, headers=ask_headers(csrf))
        assert response.status_code == 429
        # The over-quota request never entered the capability.
        assert runtime.connection_open_count == 1

    def test_admin_gains_no_extra_knowledge_path(self, tmp_path: Path) -> None:
        """ADMIN consumes the exact same public-only capability as USER."""
        app, settings, auth_runtime, runtime = build_app(tmp_path)
        provision_active_user(auth_runtime, settings, username="root", role=UserRole.ADMIN)
        client = TestClient(app)
        response = login(client, username="root")
        csrf = response.json()["csrf_token"]
        ask = client.post(ASK_URL, json={"question": "管理员提问"}, headers=ask_headers(csrf))
        assert ask.status_code == 200
        # Same single public-only pipeline; no second "private" service was
        # created for the ADMIN principal.
        assert runtime.service_creation_count == 1
        service = runtime.service
        assert service.last_question == "管理员提问"
