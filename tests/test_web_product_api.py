"""Phase 12D tests: web research product integration on API v2.

Covers capability selection through the real security gate, the additive
web Source DTO, URL provenance, independent web quota, permission policy,
kill-switch semantics, SSE ``researching`` stages and the personal-path
regression — all with fakes, fully offline.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_auth_api import (
    ask_headers,
    authed_client,
    build_app,
    login,
    provision_active_user,
)
from tests.test_public_sse import parse_sse
from zglab_rag.auth.models import UserRole
from zglab_rag.capabilities.contracts import (
    CapabilityResult,
    CapabilityStatus,
    EvidenceOrigin,
)
from zglab_rag.generation.contracts import (
    AnswerSource,
    GeneratedClaim,
    GenerationDiagnostics,
    GenerationResult,
    GenerationStatus,
    GroundedAnswer,
)
from zglab_rag.research.skill import ResearchProgressStage

ASK_URL = "/api/v2/ask"
STREAM_URL = "/api/v2/ask/stream"

PROVENANCE_URL = "https://example.com/article"
PROVENANCE_DOMAIN = "example.com"


def _web_answered_result(question: str) -> GenerationResult:
    """A web-origin GenerationResult whose source comes from provenance."""
    return GenerationResult(
        status=GenerationStatus.ANSWERED,
        question=question,
        answer=GroundedAnswer(
            answer="外部事实回答。",
            claims=[GeneratedClaim(text="外部事实回答。", citations=["E1"])],
            sources=[
                AnswerSource(
                    evidence_id="E1",
                    source_id=PROVENANCE_DOMAIN,
                    document_id=None,
                    chunk_id=None,
                    title="外部文章",
                    source_path=PROVENANCE_URL,
                    section_path=[],
                    score=0.0,
                    origin=EvidenceOrigin.WEB,
                    url=PROVENANCE_URL,
                    domain=PROVENANCE_DOMAIN,
                )
            ],
            insufficient_evidence=False,
        ),
        diagnostics=GenerationDiagnostics(
            retrieval_mode="web_research",
            retrieval_top_k=1,
            evidence_count=1,
            retrieval_latency_ms=10.0,
            provider="fake",
            model="fake",
            generation_latency_ms=10.0,
            total_latency_ms=20.0,
        ),
    )


class FakeWebSkill:
    """WebResearchSkill double: records calls, emits progress, no network."""

    def __init__(self, *, outcome: str = "answered") -> None:
        self.outcome = outcome
        self.calls: list[str] = []
        self.progress_stages: list[ResearchProgressStage] = []

    def answer(self, request, context, *, progress=None):
        self.calls.append(request.question)

        def notify(stage: ResearchProgressStage) -> None:
            self.progress_stages.append(stage)
            if progress is not None:
                progress(stage)

        notify(ResearchProgressStage.SEARCHING)
        notify(ResearchProgressStage.FETCHING)
        notify(ResearchProgressStage.EXTRACTING)
        if self.outcome == "technical_failure":
            return CapabilityResult(
                capability_id="web_research",
                status=CapabilityStatus.FAILED,
                origin=EvidenceOrigin.WEB,
                generation=None,
                failure_reason="research_provider_unavailable",
            )
        notify(ResearchProgressStage.GENERATING)
        notify(ResearchProgressStage.VALIDATING)
        return CapabilityResult(
            capability_id="web_research",
            status=CapabilityStatus.SUCCESS,
            origin=EvidenceOrigin.WEB,
            generation=_web_answered_result(request.question),
        )


def _web_client(
    tmp_path: Path,
    *,
    web_skill: FakeWebSkill | None = None,
    **overrides,
):
    defaults = dict(web_research_enabled=True)
    defaults.update(overrides)
    client, app, settings, auth_runtime, runtime, csrf = authed_client(
        tmp_path, **defaults
    )
    skill = web_skill or FakeWebSkill()
    runtime.web_research_skill = skill
    return client, runtime, skill, csrf, app, settings, auth_runtime


# ---------------------------------------------------------------------------
# Selection through the real gate
# ---------------------------------------------------------------------------


class TestProductSelection:
    def test_default_question_only_request_stays_personal(self, tmp_path: Path) -> None:
        client, runtime, skill, csrf, *_ = _web_client(tmp_path)
        response = client.post(
            ASK_URL, json={"question": "什么是 RAG？"}, headers=ask_headers(csrf)
        )
        assert response.status_code == 200
        assert response.json()["status"] == "answered"
        assert response.json()["sources"][0]["origin"] == "personal"
        assert skill.calls == []  # web skill never touched
        assert runtime.service.call_count == 1

    def test_explicit_web_executes_web_skill(self, tmp_path: Path) -> None:
        client, _runtime, skill, csrf, *_ = _web_client(tmp_path)
        response = client.post(
            ASK_URL,
            json={"question": "什么是 RAG？", "mode": "web"},
            headers=ask_headers(csrf),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "answered"
        assert skill.calls == ["什么是 RAG？"]
        source = body["sources"][0]
        assert source["origin"] == "web"
        assert source["url"] == PROVENANCE_URL
        assert source["domain"] == PROVENANCE_DOMAIN

    def test_explicit_personal_blocks_auto_web_intent(self, tmp_path: Path) -> None:
        client, _runtime, skill, csrf, *_ = _web_client(tmp_path)
        response = client.post(
            ASK_URL,
            json={"question": "Python 最新版本是什么？", "mode": "personal"},
            headers=ask_headers(csrf),
        )
        assert response.status_code == 200
        assert skill.calls == []
        assert response.json()["sources"][0]["origin"] == "personal"

    def test_auto_current_question_goes_web(self, tmp_path: Path) -> None:
        client, _runtime, skill, csrf, *_ = _web_client(tmp_path)
        response = client.post(
            ASK_URL,
            json={"question": "Python 最新版本是什么？"},
            headers=ask_headers(csrf),
        )
        assert response.status_code == 200
        assert len(skill.calls) == 1
        assert response.json()["sources"][0]["origin"] == "web"

    def test_auto_personal_question_never_triggers_web(self, tmp_path: Path) -> None:
        client, _runtime, skill, csrf, *_ = _web_client(tmp_path)
        response = client.post(
            ASK_URL, json={"question": "我做过哪些项目？"}, headers=ask_headers(csrf)
        )
        assert response.status_code == 200
        assert skill.calls == []
        assert response.json()["sources"][0]["origin"] == "personal"

    def test_invalid_mode_rejected(self, tmp_path: Path) -> None:
        client, _runtime, _skill, csrf, *_ = _web_client(tmp_path)
        response = client.post(
            ASK_URL,
            json={"question": "什么是 RAG？", "mode": "planner"},
            headers=ask_headers(csrf),
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Security gate precedence: AuthN / AuthZ / CSRF before selection
# ---------------------------------------------------------------------------


class TestGatePrecedence:
    def test_anonymous_never_reaches_selection(self, tmp_path: Path) -> None:
        _client, _runtime, skill, _csrf, app, *_ = _web_client(tmp_path)
        # Fresh client without any session cookie: truly anonymous.
        anonymous = TestClient(app)
        response = anonymous.post(
            ASK_URL,
            json={"question": "Python 最新版本是什么？", "mode": "web"},
            headers={"origin": "http://testserver"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
        assert skill.calls == []

    def test_csrf_failure_never_reaches_selection(self, tmp_path: Path) -> None:
        client, _runtime, skill, _csrf, *_ = _web_client(tmp_path)
        response = client.post(
            ASK_URL,
            json={"question": "Python 最新版本是什么？", "mode": "web"},
            headers={"origin": "http://testserver", "x-csrf-token": "wrong"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "CSRF_REJECTED"
        assert skill.calls == []


# ---------------------------------------------------------------------------
# Kill switch & permission policy (server-side)
# ---------------------------------------------------------------------------


class TestKillSwitchAndPermission:
    def test_explicit_web_disabled_returns_capability_disabled(self, tmp_path: Path) -> None:
        client, _app, _settings, _auth_runtime, runtime, csrf = authed_client(
            tmp_path, web_research_enabled=False
        )
        skill = FakeWebSkill()
        runtime.web_research_skill = skill
        response = client.post(
            ASK_URL,
            json={"question": "Python 最新版本是什么？", "mode": "web"},
            headers=ask_headers(csrf),
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "CAPABILITY_DISABLED"
        assert skill.calls == []

    def test_personal_fully_works_while_web_disabled(self, tmp_path: Path) -> None:
        client, _app, _settings, _auth_runtime, _runtime, csrf = authed_client(
            tmp_path, web_research_enabled=False
        )
        response = client.post(
            ASK_URL, json={"question": "什么是 RAG？"}, headers=ask_headers(csrf)
        )
        assert response.status_code == 200
        assert response.json()["status"] == "answered"

    def test_auto_web_degrades_to_personal_while_disabled(self, tmp_path: Path) -> None:
        client, _app, _settings, _auth_runtime, runtime, csrf = authed_client(
            tmp_path, web_research_enabled=False
        )
        skill = FakeWebSkill()
        runtime.web_research_skill = skill
        response = client.post(
            ASK_URL, json={"question": "Python 最新版本是什么？"}, headers=ask_headers(csrf)
        )
        assert response.status_code == 200
        assert skill.calls == []
        assert response.json()["sources"][0]["origin"] == "personal"

    def test_admin_only_policy_denies_regular_user(self, tmp_path: Path) -> None:
        client, _runtime, skill, csrf, *_ = _web_client(
            tmp_path, web_research_admin_only=True
        )
        response = client.post(
            ASK_URL,
            json={"question": "Python 最新版本是什么？", "mode": "web"},
            headers=ask_headers(csrf),
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "CAPABILITY_DENIED"
        assert skill.calls == []

    def test_admin_only_policy_allows_admin(self, tmp_path: Path) -> None:
        app, settings, auth_runtime, runtime = build_app(
            tmp_path,
            web_research_enabled=True,
            web_research_admin_only=True,
        )
        provision_active_user(
            auth_runtime, settings, username="root", role=UserRole.ADMIN
        )
        client = TestClient(app)
        response = login(client, username="root")
        assert response.status_code == 200
        csrf = response.json()["csrf_token"]
        skill = FakeWebSkill()
        runtime.web_research_skill = skill
        allowed = client.post(
            ASK_URL,
            json={"question": "Python 最新版本是什么？", "mode": "web"},
            headers=ask_headers(csrf),
        )
        assert allowed.status_code == 200
        assert len(skill.calls) == 1


# ---------------------------------------------------------------------------
# Independent web quota & accounting
# ---------------------------------------------------------------------------


class TestWebQuotaBoundary:
    def test_web_quota_independent_from_personal_bucket(self, tmp_path: Path) -> None:
        client, _runtime, _skill, csrf, *_ = _web_client(
            tmp_path,
            web_research_requests_per_minute=1,
            web_research_requests_per_day=2,
        )
        first = client.post(
            ASK_URL,
            json={"question": "Python 最新版本是什么？", "mode": "web"},
            headers=ask_headers(csrf),
        )
        assert first.status_code == 200
        second = client.post(
            ASK_URL,
            json={"question": "FastAPI 最新版本是什么？", "mode": "web"},
            headers=ask_headers(csrf),
        )
        assert second.status_code == 429
        assert second.json()["error"]["code"] == "QUOTA_EXCEEDED"
        # Personal asks keep working: separate bucket, not exhausted by web.
        personal = client.post(
            ASK_URL, json={"question": "什么是 RAG？"}, headers=ask_headers(csrf)
        )
        assert personal.status_code == 200

    def test_personal_quota_never_charged_by_web_requests(self, tmp_path: Path) -> None:
        client, _runtime, _skill, csrf, _app, settings, auth_runtime = _web_client(
            tmp_path,
            auth_user_requests_per_minute=1,
            web_research_requests_per_minute=5,
        )
        # Web request first: must not consume the personal minute bucket.
        web = client.post(
            ASK_URL,
            json={"question": "Python 最新版本是什么？", "mode": "web"},
            headers=ask_headers(csrf),
        )
        assert web.status_code == 200
        personal = client.post(
            ASK_URL, json={"question": "什么是 RAG？"}, headers=ask_headers(csrf)
        )
        assert personal.status_code == 200
        # Now the personal bucket is exhausted by the personal ask itself.
        denied = client.post(
            ASK_URL, json={"question": "向量检索是什么？"}, headers=ask_headers(csrf)
        )
        assert denied.status_code == 429

    def test_rejected_requests_consume_no_web_quota(self, tmp_path: Path) -> None:
        client, _runtime, _skill, csrf, *_ = _web_client(
            tmp_path, web_research_requests_per_minute=1
        )
        # CSRF failure: no quota consumed.
        bad = client.post(
            ASK_URL,
            json={"question": "Python 最新版本是什么？", "mode": "web"},
            headers={"origin": "http://testserver", "x-csrf-token": "wrong"},
        )
        assert bad.status_code == 403
        ok = client.post(
            ASK_URL,
            json={"question": "Python 最新版本是什么？", "mode": "web"},
            headers=ask_headers(csrf),
        )
        assert ok.status_code == 200


# ---------------------------------------------------------------------------
# Failure semantics & SSE
# ---------------------------------------------------------------------------


class TestWebFailureAndSse:
    def test_research_technical_failure_maps_to_provider_unavailable(
        self, tmp_path: Path
    ) -> None:
        skill = FakeWebSkill(outcome="technical_failure")
        client, _runtime, _skill, csrf, *_ = _web_client(tmp_path, web_skill=skill)
        response = client.post(
            ASK_URL,
            json={"question": "Python 最新版本是什么？", "mode": "web"},
            headers=ask_headers(csrf),
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"

    def test_sse_web_path_emits_researching_stage(self, tmp_path: Path) -> None:
        client, _runtime, skill, csrf, *_ = _web_client(tmp_path)
        with client.stream(
            "POST",
            STREAM_URL,
            json={"question": "Python 最新版本是什么？", "mode": "web"},
            headers=ask_headers(csrf),
        ) as response:
            text = response.read().decode("utf-8")
        events, _ = parse_sse(text)
        stages = [payload["stage"] for name, payload in events if name != "completed"]
        assert stages == ["accepted", "researching", "generating", "validating"]
        completed = dict(events)["completed"]
        assert completed["status"] == "answered"
        assert completed["sources"][0]["origin"] == "web"
        assert completed["sources"][0]["url"] == PROVENANCE_URL
        # Research progress reached the skill through the public adapter.
        assert ResearchProgressStage.SEARCHING in skill.progress_stages

    def test_sse_personal_path_never_emits_researching(self, tmp_path: Path) -> None:
        client, _runtime, _skill, csrf, *_ = _web_client(tmp_path)
        with client.stream(
            "POST",
            STREAM_URL,
            json={"question": "什么是 RAG？"},
            headers=ask_headers(csrf),
        ) as response:
            text = response.read().decode("utf-8")
        events, _ = parse_sse(text)
        stages = [payload["stage"] for name, payload in events if name != "completed"]
        # The personal path must never expose the web researching stage;
        # the fake personal service emits no intermediate stages, so the
        # stream stays accepted + completed.
        assert "researching" not in stages
        completed = dict(events)["completed"]
        assert completed["status"] == "answered"
        assert completed["sources"][0]["origin"] == "personal"

    def test_sse_explicit_web_disabled_fails_before_stream(self, tmp_path: Path) -> None:
        client, _app, _settings, _auth_runtime, runtime, csrf = authed_client(
            tmp_path, web_research_enabled=False
        )
        runtime.web_research_skill = FakeWebSkill()
        response = client.post(
            STREAM_URL,
            json={"question": "Python 最新版本是什么？", "mode": "web"},
            headers=ask_headers(csrf),
        )
        # Pre-stream rejections stay plain JSON, never an SSE stream.
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "CAPABILITY_DISABLED"
