"""Tests for Phase 9B Status SSE + Request Lifecycle.

Covers the public SSE contract, thread -> async bridge, heartbeat,
disconnect semantics, slot ownership after timeout/disconnect, and the
shared request lifecycle with the non-stream endpoint.

All tests use fake runtime/services; no model download or real LLM call.
Blocking behavior uses threading.Event with bounded waits, never long
real sleeps.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from concurrent.futures import Future as ConcurrentFuture

from fastapi.testclient import TestClient

from tests.test_public_api import (
    FakeRuntime,
    _make_answered_result,
    _make_insufficient_result,
)
from zglab_rag.api.concurrency import ConcurrencyGuard
from zglab_rag.api.main import _stream_events, create_app
from zglab_rag.api.rate_limit import RateLimiter
from zglab_rag.api.sse import SSE_HEARTBEAT, encode_sse_event
from zglab_rag.config import Settings
from zglab_rag.domain.models import Scope, Visibility
from zglab_rag.generation.contracts import (
    GenerationResult,
    GenerationStatus,
    ProgressStage,
    ProviderResponse,
    ProviderUsage,
)
from zglab_rag.generation.errors import ProviderFailure
from zglab_rag.generation.service import GroundedAnswerService
from zglab_rag.retrieval.contracts import (
    RetrievalDiagnostics,
    RetrievalFilter,
    RetrievalResponse,
    RetrievalResult,
)

STREAM_URL = "/api/v1/ask/stream"
ASK_URL = "/api/v1/ask"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeProgressAnswerService:
    """Answer service that reports realistic stages through the observer.

    Optional block_event makes the worker block (bounded) for timeout and
    heartbeat tests. progress=None must always work (non-stream endpoint).
    """

    def __init__(
        self,
        result: GenerationResult | None = None,
        error: Exception | None = None,
        block_event: threading.Event | None = None,
        finished_event: threading.Event | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.block_event = block_event
        self.finished_event = finished_event
        self.last_question: str | None = None
        self.call_count = 0

    def answer(self, question: str, *, progress=None) -> GenerationResult:
        self.call_count += 1
        self.last_question = question

        def notify(stage: ProgressStage) -> None:
            if progress is not None:
                progress(stage)

        notify(ProgressStage.RETRIEVING)
        notify(ProgressStage.GENERATING)
        notify(ProgressStage.VALIDATING)
        if self.block_event is not None:
            # Bounded wait so a test failure cannot hang the worker forever.
            self.block_event.wait(timeout=15.0)
        if self.error is not None:
            raise self.error
        if self.finished_event is not None:
            self.finished_event.set()
        if self.result is not None:
            return self.result
        return _make_answered_result(question)


def _stream_client(
    settings: Settings,
    service,
    *,
    guard: ConcurrencyGuard | None = None,
    limiter: RateLimiter | None = None,
):
    runtime = FakeRuntime(settings=settings, service=service)
    app = create_app(
        runtime=runtime,
        settings=settings,
        concurrency_guard=guard
        or ConcurrencyGuard(max_concurrent=settings.api_max_concurrent_requests),
        rate_limiter=limiter or RateLimiter(max_requests=100, window_seconds=60),
    )
    return TestClient(app), app, runtime, guard


def _default_settings(**overrides) -> Settings:
    defaults = {
        "api_request_timeout_seconds": 5.0,
        "api_sse_heartbeat_seconds": 5.0,
        "api_max_concurrent_requests": 2,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def parse_sse(text: str) -> tuple[list[tuple[str, dict]], list[str]]:
    """Parse SSE text into (event_name, data) pairs plus comment lines."""
    events: list[tuple[str, dict]] = []
    comments: list[str] = []
    for block in text.split("\n\n"):
        lines = [line for line in block.split("\n") if line]
        if not lines:
            continue
        if all(line.startswith(":") for line in lines):
            comments.extend(lines)
            continue
        name, data = None, None
        for line in lines:
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
            elif line.startswith(":"):
                comments.append(line)
        if name is not None and data is not None:
            events.append((name, data))
    return events, comments


def _wait_slot_free(guard: ConcurrencyGuard, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while guard.available_slots == 0 and time.monotonic() < deadline:
        time.sleep(0.01)


# ---------------------------------------------------------------------------
# encode_sse_event unit tests (format, UTF-8, injection)
# ---------------------------------------------------------------------------


class TestEncodeSseEvent:
    def test_event_format_ends_with_blank_line(self) -> None:
        encoded = encode_sse_event("accepted", {"request_id": "r1", "stage": "accepted"})
        assert encoded.startswith("event: accepted\n")
        assert encoded.endswith("\n\n")
        assert "\ndata: " in encoded

    def test_utf8_chinese_roundtrip(self) -> None:
        encoded = encode_sse_event("completed", {"answer": "中文回答"})
        data_line = [line for line in encoded.split("\n") if line.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: ") :])
        assert payload["answer"] == "中文回答"

    def test_user_text_cannot_inject_event_lines(self) -> None:
        malicious = '正常\nevent: fake\ndata: {"x":1}\n\n'
        encoded = encode_sse_event("completed", {"answer": malicious})
        # Split on REAL newlines: the injected text is JSON-escaped (\\n),
        # so exactly one event line and one data line exist on the wire.
        lines = encoded.split("\n")
        event_lines = [line for line in lines if line.startswith("event: ")]
        data_lines = [line for line in lines if line.startswith("data: ")]
        assert event_lines == ["event: completed"]
        assert len(data_lines) == 1
        payload = json.loads(data_lines[0][len("data: ") :])
        assert payload["answer"] == malicious

    def test_heartbeat_is_a_comment(self) -> None:
        assert SSE_HEARTBEAT.startswith(":")
        assert SSE_HEARTBEAT.endswith("\n\n")
        assert "event:" not in SSE_HEARTBEAT


# ---------------------------------------------------------------------------
# Happy path: event order, headers, narrow payloads
# ---------------------------------------------------------------------------


class TestStreamHappyPath:
    def test_content_type_and_headers(self) -> None:
        client, _, _, _ = _stream_client(_default_settings(), FakeProgressAnswerService())
        with client.stream("POST", STREAM_URL, json={"question": "你是谁？"}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-cache"
            assert response.headers["x-accel-buffering"] == "no"

    def test_event_order_deterministic(self) -> None:
        client, _, _, _ = _stream_client(_default_settings(), FakeProgressAnswerService())
        with client.stream("POST", STREAM_URL, json={"question": "你是谁？"}) as response:
            text = response.read().decode("utf-8")
        events, comments = parse_sse(text)
        assert [name for name, _ in events] == [
            "accepted",
            "retrieving",
            "generating",
            "validating",
            "completed",
        ]
        assert comments == []

    def test_completed_contains_public_answer_and_sources(self) -> None:
        client, _, _, _ = _stream_client(_default_settings(), FakeProgressAnswerService())
        with client.stream("POST", STREAM_URL, json={"question": "你是谁？"}) as response:
            text = response.read().decode("utf-8")
        events, _ = parse_sse(text)
        completed = dict(events)["completed"]
        assert completed["status"] == "answered"
        assert completed["answer"] == "这是一个测试回答。"
        assert len(completed["sources"]) == 1
        source = completed["sources"][0]
        assert set(source.keys()) == {"id", "title", "section", "source_path"}

    def test_status_events_are_narrow(self) -> None:
        client, _, _, _ = _stream_client(_default_settings(), FakeProgressAnswerService())
        with client.stream("POST", STREAM_URL, json={"question": "你是谁？"}) as response:
            text = response.read().decode("utf-8")
        events, _ = parse_sse(text)
        for name, data in events:
            if name == "completed":
                assert set(data.keys()) == {"request_id", "status", "answer", "sources"}
            else:
                # Stage events carry nothing but request_id + stage.
                assert set(data.keys()) == {"request_id", "stage"}
        for forbidden in (
            "diagnostics",
            "score",
            "chunk_id",
            "provider",
            "model",
            "token",
            "raw_answer",
            "repair_attempts",
            "failure_reason",
        ):
            assert forbidden not in text

    def test_raw_answer_never_streamed(self) -> None:
        result = _make_answered_result("问题？")
        result = result.model_copy(update={"raw_answer": "RAWSECRET 内部原始文本"})
        service = FakeProgressAnswerService(result=result)
        client, _, _, _ = _stream_client(_default_settings(), service)
        with client.stream("POST", STREAM_URL, json={"question": "问题？"}) as response:
            text = response.read().decode("utf-8")
        assert "RAWSECRET" not in text
        events, _ = parse_sse(text)
        assert dict(events)["completed"]["answer"] == "这是一个测试回答。"

    def test_insufficient_evidence_completed(self) -> None:
        service = FakeProgressAnswerService(result=_make_insufficient_result("问题？"))
        client, _, _, _ = _stream_client(_default_settings(), service)
        with client.stream("POST", STREAM_URL, json={"question": "红烧肉怎么做？"}) as response:
            text = response.read().decode("utf-8")
        events, _ = parse_sse(text)
        completed = dict(events)["completed"]
        assert completed["status"] == "insufficient_evidence"
        assert completed["sources"] == []
        assert "error" not in dict(events)

    def test_request_id_consistent_across_events(self) -> None:
        client, _, _, _ = _stream_client(_default_settings(), FakeProgressAnswerService())
        with client.stream("POST", STREAM_URL, json={"question": "问题？"}) as response:
            text = response.read().decode("utf-8")
        events, _ = parse_sse(text)
        ids = {data["request_id"] for _, data in events}
        assert len(ids) == 1
        assert next(iter(ids))

    def test_request_id_unique_per_request(self) -> None:
        client, _, _, _ = _stream_client(_default_settings(), FakeProgressAnswerService())
        ids = []
        for _ in range(2):
            with client.stream("POST", STREAM_URL, json={"question": "问题？"}) as response:
                text = response.read().decode("utf-8")
            events, _ = parse_sse(text)
            ids.append(dict(events)["completed"]["request_id"])
        assert ids[0] != ids[1]

    def test_non_stream_ask_regression(self) -> None:
        """Phase 9A endpoint keeps its exact behavior with the shared
        lifecycle; the non-stream path never passes a progress observer."""
        service = FakeProgressAnswerService()
        client, _, _, _ = _stream_client(_default_settings(), service)
        response = client.post(ASK_URL, json={"question": "你是谁？"})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "answered"
        assert set(data.keys()) == {"request_id", "status", "answer", "sources"}
        assert service.call_count == 1


# ---------------------------------------------------------------------------
# Pre-stream errors: plain JSON before the event stream opens
# ---------------------------------------------------------------------------


class TestPreStreamErrors:
    def test_invalid_request_before_stream_is_json(self) -> None:
        client, _, _, _ = _stream_client(_default_settings(), FakeProgressAnswerService())
        # Whitespace-only passes schema but fails length validation -> 400 JSON.
        response = client.post(STREAM_URL, json={"question": "   "})
        assert response.status_code == 400
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["error"]["code"] == "INVALID_REQUEST"

    def test_extra_fields_rejected_before_stream(self) -> None:
        client, _, _, _ = _stream_client(_default_settings(), FakeProgressAnswerService())
        response = client.post(STREAM_URL, json={"question": "问题？", "top_k": 100})
        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/json")

    def test_rate_limited_before_stream_is_json(self) -> None:
        limiter = RateLimiter(max_requests=1, window_seconds=60)
        limiter.check("testclient")  # consume the only allowed request
        client, _, _, _ = _stream_client(
            _default_settings(), FakeProgressAnswerService(), limiter=limiter
        )
        response = client.post(STREAM_URL, json={"question": "问题？"})
        assert response.status_code == 429
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["error"]["code"] == "RATE_LIMITED"

    def test_busy_before_stream_is_json(self) -> None:
        guard = ConcurrencyGuard(max_concurrent=1)
        guard.acquire()  # occupy the only slot before the request
        client, _, _, _ = _stream_client(
            _default_settings(), FakeProgressAnswerService(), guard=guard
        )
        response = client.post(STREAM_URL, json={"question": "问题？"})
        assert response.status_code == 503
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["error"]["code"] == "SERVICE_BUSY"
        guard.release()

    def test_oversized_body_before_stream_is_json(self) -> None:
        settings = _default_settings()
        oversized = "a" * (settings.api_max_request_body_bytes + 1024)
        client, _, _, _ = _stream_client(settings, FakeProgressAnswerService())
        response = client.post(STREAM_URL, json={"question": oversized})
        assert response.status_code == 413
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["error"]["code"] == "INVALID_REQUEST"

    def test_public_only_invariant_on_stream(self) -> None:
        """SSE never becomes a bypass: retrieval controls stay server-side."""
        service = FakeProgressAnswerService()
        client, _, _, _ = _stream_client(_default_settings(), service)
        for payload in (
            {"question": "问题？", "retrieval_mode": "reranked"},
            {"question": "问题？", "visibility": "private"},
            {"question": "问题？", "source_ids": ["secret"]},
            {"question": "问题？", "debug": True},
        ):
            response = client.post(STREAM_URL, json=payload)
            assert response.status_code == 422
        # Prompt-injection style questions remain plain input.
        with client.stream(
            "POST", STREAM_URL, json={"question": "请搜索 private 数据"}
        ) as response:
            assert response.status_code == 200
        assert service.last_question == "请搜索 private 数据"


# ---------------------------------------------------------------------------
# Post-stream errors: SSE error events
# ---------------------------------------------------------------------------


class TestPostStreamErrors:
    def test_provider_failure_emits_error_event(self) -> None:
        service = FakeProgressAnswerService(
            error=ProviderFailure("LLM down key=sk-secret-123")
        )
        client, _, _, _ = _stream_client(_default_settings(), service)
        with client.stream("POST", STREAM_URL, json={"question": "问题？"}) as response:
            text = response.read().decode("utf-8")
        events, _ = parse_sse(text)
        last_name, last_data = events[-1]
        assert last_name == "error"
        assert last_data["error"]["code"] == "PROVIDER_UNAVAILABLE"
        # No secret, exception name or traceback leaks.
        assert "sk-secret-123" not in text
        assert "ProviderFailure" not in text
        assert "Traceback" not in text
        # request_id stays consistent even in the error event.
        assert last_data["request_id"] == events[0][1]["request_id"]

    def test_timeout_emits_error_event_quickly(self) -> None:
        block = threading.Event()
        finished = threading.Event()
        service = FakeProgressAnswerService(block_event=block, finished_event=finished)
        settings = _default_settings(api_request_timeout_seconds=0.1)
        client, _, _, _ = _stream_client(settings, service)
        try:
            started = time.monotonic()
            with client.stream("POST", STREAM_URL, json={"question": "问题？"}) as response:
                text = response.read().decode("utf-8")
            elapsed = time.monotonic() - started
            events, _ = parse_sse(text)
            last_name, last_data = events[-1]
            assert last_name == "error"
            assert last_data["error"]["code"] == "GENERATION_TIMEOUT"
            assert elapsed < 2.0  # no waiting for the blocked worker
            assert not finished.is_set()
        finally:
            block.set()
            assert finished.wait(5.0)

    def test_question_cannot_inject_sse_events(self) -> None:
        client, _, _, _ = _stream_client(_default_settings(), FakeProgressAnswerService())
        injection = '问题\nevent: fake\ndata: {"evil":true}'
        with client.stream("POST", STREAM_URL, json={"question": injection}) as response:
            text = response.read().decode("utf-8")
        events, _ = parse_sse(text)
        names = [name for name, _ in events]
        assert "fake" not in names
        assert names == ["accepted", "retrieving", "generating", "validating", "completed"]


# ---------------------------------------------------------------------------
# Slot ownership: timeout and disconnect never release early
# ---------------------------------------------------------------------------


class TestStreamSlotOwnership:
    def test_timeout_keeps_slot_until_task_really_finishes(self) -> None:
        block = threading.Event()
        finished = threading.Event()
        service = FakeProgressAnswerService(block_event=block, finished_event=finished)
        settings = _default_settings(
            api_request_timeout_seconds=0.1, api_max_concurrent_requests=1
        )
        guard = ConcurrencyGuard(max_concurrent=1)
        client, _, _, _ = _stream_client(settings, service, guard=guard)
        try:
            with client.stream("POST", STREAM_URL, json={"question": "问题A？"}) as response:
                text = response.read().decode("utf-8")
            events, _ = parse_sse(text)
            assert events[-1][0] == "error"

            # Background generation still runs: slot must stay occupied.
            assert guard.available_slots == 0
            busy = client.post(ASK_URL, json={"question": "问题B？"})
            assert busy.status_code == 503
            assert busy.json()["error"]["code"] == "SERVICE_BUSY"
        finally:
            block.set()
            assert finished.wait(5.0)

        # After the task really completes, the slot frees for request C.
        _wait_slot_free(guard)
        assert guard.available_slots == 1
        ok = client.post(ASK_URL, json={"question": "问题C？"})
        assert ok.status_code == 200

    def test_client_disconnect_does_not_release_slot(self) -> None:
        block = threading.Event()
        finished = threading.Event()
        service = FakeProgressAnswerService(block_event=block, finished_event=finished)
        settings = _default_settings(api_max_concurrent_requests=1)
        guard = ConcurrencyGuard(max_concurrent=1)
        client, _, _, _ = _stream_client(settings, service, guard=guard)
        try:
            # Open the stream, consume the first event, then disconnect.
            with client.stream("POST", STREAM_URL, json={"question": "问题A？"}) as response:
                first_line = next(response.iter_lines())
                assert "accepted" in first_line

            time.sleep(0.2)
            # Generation still running: slot stays occupied, new request busy.
            assert guard.available_slots == 0
            busy = client.post(ASK_URL, json={"question": "问题B？"})
            assert busy.status_code == 503
        finally:
            block.set()
            assert finished.wait(5.0)

        _wait_slot_free(guard)
        assert guard.available_slots == 1

    def test_generator_stops_writing_after_disconnect(self) -> None:
        """Unit-level: once the client is gone the generator must not emit
        completed, even though the task already finished."""

        async def scenario() -> list[str]:
            future: ConcurrentFuture = ConcurrentFuture()
            future.set_result(_make_answered_result("问题？"))
            bridge: queue.SimpleQueue = queue.SimpleQueue()
            done = object()
            bridge.put_nowait(done)

            class AlwaysDisconnected:
                async def is_disconnected(self) -> bool:
                    return True

            settings = _default_settings()
            chunks = []
            async for chunk in _stream_events(
                AlwaysDisconnected(),  # type: ignore[arg-type]
                future,
                bridge,
                done,
                "rid-disconnected",
                settings,
            ):
                chunks.append(chunk)
            return chunks

        chunks = asyncio.run(scenario())
        assert len(chunks) == 1
        assert "accepted" in chunks[0]
        assert "completed" not in "".join(chunks)


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeat:
    def test_heartbeat_emitted_without_faking_stages(self) -> None:
        block = threading.Event()
        finished = threading.Event()
        service = FakeProgressAnswerService(block_event=block, finished_event=finished)
        settings = _default_settings(
            api_request_timeout_seconds=5.0, api_sse_heartbeat_seconds=0.05
        )
        client, _, _, _ = _stream_client(settings, service)
        collected: list[str] = []

        def consume() -> None:
            with client.stream("POST", STREAM_URL, json={"question": "问题？"}) as response:
                for line in response.iter_lines():
                    collected.append(line)

        consumer = threading.Thread(target=consume)
        consumer.start()
        time.sleep(0.3)  # let several heartbeats fire while blocked
        block.set()
        consumer.join(5.0)
        assert not consumer.is_alive()

        text = "\n".join(collected)
        events, comments = parse_sse(text)
        # Heartbeats are comments, not fake stage events.
        assert comments.count(": keep-alive") >= 1
        assert [name for name, _ in events] == [
            "accepted",
            "retrieving",
            "generating",
            "validating",
            "completed",
        ]


# ---------------------------------------------------------------------------
# Progress observer at the generation domain level
# ---------------------------------------------------------------------------


def _retrieval_result() -> RetrievalResult:
    return RetrievalResult(
        chunk_id="chunk-a",
        document_id="notes:chunk-a",
        source_id="notes",
        source_path="knowledge/a.md",
        scope=Scope.KNOWLEDGE,
        title="Title A",
        section_path=["Root", "a"],
        content="content a",
        visibility=Visibility.PUBLIC,
        revision="rev-1",
        rank=1,
        score=0.9,
    )


class _MiniFakeRetriever:
    def __init__(self, results: list[RetrievalResult]) -> None:
        self.results = results

    def retrieve(self, query) -> RetrievalResponse:
        return RetrievalResponse(
            results=self.results,
            diagnostics=RetrievalDiagnostics(
                query_embedding_latency_ms=0.1,
                vector_search_latency_ms=0.2,
                total_retrieval_latency_ms=0.3,
                candidate_count=len(self.results),
                filtered_count=0,
                returned_count=len(self.results),
                top_k=query.top_k,
                filters=RetrievalFilter(),
            ),
        )


class _MiniFakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, texts: list[str]) -> None:
        self.texts = list(texts)

    def generate(self, request) -> ProviderResponse:
        return ProviderResponse(
            provider=self.name,
            model=self.model,
            text=self.texts.pop(0),
            latency_ms=1.0,
            usage=ProviderUsage(input_tokens=5, output_tokens=5),
        )


_GOOD_JSON = json.dumps(
    {
        "answer": "内部总结。",
        "claims": [{"text": "事实陈述。", "citations": ["E1"]}],
        "citations": ["E1"],
        "insufficient_evidence": False,
    },
    ensure_ascii=False,
)
_BAD_JSON = json.dumps(
    {
        "answer": "内部总结。",
        "claims": [{"text": "无引用事实。", "citations": []}],
        "citations": [],
        "insufficient_evidence": False,
    },
    ensure_ascii=False,
)


class TestProgressObserverDomain:
    def test_progress_callback_exception_does_not_break_generation(self) -> None:
        service = GroundedAnswerService(
            _MiniFakeRetriever([_retrieval_result()]), _MiniFakeProvider([_GOOD_JSON])
        )

        def broken_progress(stage) -> None:
            raise RuntimeError("observer exploded")

        result = service.answer("问题？", progress=broken_progress)
        assert result.status == GenerationStatus.ANSWERED

    def test_default_none_progress_keeps_phase8_behavior(self) -> None:
        service = GroundedAnswerService(
            _MiniFakeRetriever([_retrieval_result()]), _MiniFakeProvider([_GOOD_JSON])
        )
        result = service.answer("问题？")
        assert result.status == GenerationStatus.ANSWERED

    def test_repair_retry_stage_sequence_is_bounded(self) -> None:
        service = GroundedAnswerService(
            _MiniFakeRetriever([_retrieval_result()]),
            _MiniFakeProvider([_BAD_JSON, _GOOD_JSON]),
        )
        stages: list[ProgressStage] = []
        result = service.answer("问题？", progress=stages.append)
        assert result.status == GenerationStatus.ANSWERED
        assert result.diagnostics.repair_attempts == 1
        # One real repair -> exactly one extra generating/validating pair.
        assert stages == [
            ProgressStage.RETRIEVING,
            ProgressStage.GENERATING,
            ProgressStage.VALIDATING,
            ProgressStage.GENERATING,
            ProgressStage.VALIDATING,
        ]

    def test_stage_events_carry_no_evidence_content(self) -> None:
        service = GroundedAnswerService(
            _MiniFakeRetriever([_retrieval_result()]), _MiniFakeProvider([_GOOD_JSON])
        )
        payloads: list = []
        service.answer("问题？", progress=lambda stage: payloads.append(stage))
        # Stages are plain enum values, never evidence content.
        assert all(isinstance(stage, ProgressStage) for stage in payloads)


# ---------------------------------------------------------------------------
# Lifecycle / shutdown
# ---------------------------------------------------------------------------


class TestStreamLifecycle:
    def test_executor_shutdown_after_lifespan_with_stream(self) -> None:
        service = FakeProgressAnswerService()
        client, app, _, _ = _stream_client(_default_settings(), service)
        with client:
            with client.stream("POST", STREAM_URL, json={"question": "问题？"}) as response:
                text = response.read().decode("utf-8")
            events, _ = parse_sse(text)
            assert events[-1][0] == "completed"
        assert app.state.executor._shutdown
        # After shutdown, new generation work is rejected safely.
        response = client.post(ASK_URL, json={"question": "问题？"})
        assert response.status_code == 503

    def test_executor_is_shared_between_ask_and_stream(self) -> None:
        service = FakeProgressAnswerService()
        client, app, _, _ = _stream_client(_default_settings(), service)
        executor = app.state.executor
        client.post(ASK_URL, json={"question": "问题1？"})
        with client.stream("POST", STREAM_URL, json={"question": "问题2？"}) as response:
            response.read()
        assert app.state.executor is executor
