"""FastAPI application factory and public API endpoints.

This module provides the create_app factory for constructing the FastAPI
application with dependency injection support for testing. The factory
pattern allows tests to inject fake runtimes without requiring real
databases, embedding models, or LLM providers.

Phase 9A implements:
- POST /api/v1/ask (narrow public contract)
- GET /health (lightweight liveness)
- GET /sources (public source metadata, sanitized)

Phase 9B adds:
- POST /api/v1/ask/stream (status SSE, not raw token streaming)

Both ask endpoints share one request lifecycle: the same schema validation,
rate limit, concurrency guard, timeout and public-only security invariant.
The final answer is only ever sent after structured generation, citation
validation and deterministic rendering.

Security boundaries:
- Public-only retrieval (visibility=public is enforced server-side)
- No client control over retrieval_mode, top_k, visibility
- Request/response contracts are narrow; no internal diagnostics leak
- Concurrency guard and rate limiter protect against overload
"""

from __future__ import annotations

import asyncio
import logging
import queue
import uuid
from collections.abc import AsyncIterator
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from contextlib import asynccontextmanager, contextmanager
from time import perf_counter
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from zglab_rag.api.runtime import ProductionRuntime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError

from zglab_rag import __version__
from zglab_rag.api.concurrency import ConcurrencyGuard, ServiceBusyError
from zglab_rag.api.contracts import (
    PublicAskRequest,
    PublicAskResponse,
    PublicErrorCode,
    PublicErrorDetail,
    PublicErrorResponse,
    PublicSource,
    PublicStatus,
    PublicStreamStage,
    PublicStreamStatus,
)
from zglab_rag.api.observability import log_http_request
from zglab_rag.api.rate_limit import RateLimiter, RateLimitExceededError
from zglab_rag.api.sse import SSE_HEADERS, SSE_HEARTBEAT, encode_sse_event
from zglab_rag.config import Settings, get_settings
from zglab_rag.generation.contracts import (
    GenerationResult,
    GenerationStatus,
    ProgressCallback,
    ProgressStage,
)
from zglab_rag.generation.errors import GenerationError, ProviderFailure
from zglab_rag.sources.registry import SourceRegistry

logger = logging.getLogger(__name__)


def _configure_production_logging() -> None:
    """Ensure JSON application records reach systemd's standard-error stream."""
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("zglab_rag").setLevel(logging.INFO)
    for library_logger in ("sentence_transformers", "transformers", "huggingface_hub"):
        logging.getLogger(library_logger).setLevel(logging.WARNING)


class AnswerService(Protocol):
    """Protocol for the answer service (real or fake).

    The optional progress observer is only consumed by the SSE endpoint;
    it reports abstract stages and never carries evidence content.
    """

    def answer(
        self, question: str, *, progress: ProgressCallback | None = None
    ) -> GenerationResult: ...


class ApplicationRuntime(Protocol):
    """Protocol for the application runtime (real or fake)."""

    settings: Settings

    @contextmanager
    def request_connection(self):
        """Yield a request-scoped connection."""
        ...

    def create_service(self, connection) -> AnswerService:
        """Create an answer service for the given connection."""
        ...


def create_app(
    *,
    runtime: ApplicationRuntime | None = None,
    settings: Settings | None = None,
    concurrency_guard: ConcurrencyGuard | None = None,
    rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    """Create the FastAPI application with optional dependency injection.

    For production, call create_app() without arguments.
    For testing, inject fake runtime/guard/limiter.

    Resource lifecycle:
    - The ThreadPoolExecutor is app-scoped: created once at startup, shared
      by all requests, and shut down in the lifespan exit. Worker count is
      bounded by the concurrency baseline plus one.
    - When no runtime is injected, the production runtime (embedding model,
      LLM provider, database) is initialized eagerly at startup (fail-fast),
      so the first public request does not pay the model load cost.
    """
    settings = settings or get_settings()
    # App-scoped bounded worker pool; shared across all requests.
    executor = ThreadPoolExecutor(max_workers=settings.api_max_concurrent_requests + 1)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _configure_production_logging()
        app.state.ready = False
        app.state.startup_error = None
        started = perf_counter()
        try:
            if app.state.runtime is None:
                # Fail-fast production startup: load embedding model and validate
                # configuration before serving the first public request.
                app.state.runtime = _get_runtime()
                app.state.runtime.verify_ready()
            app.state.ready = True
            logger.info("runtime_ready startup_ms=%.3f", (perf_counter() - started) * 1000)
        except Exception:
            app.state.startup_error = "runtime_initialization_failed"
            logger.error("runtime_startup_failed error_code=RUNTIME_NOT_READY")
            raise
        try:
            yield
        finally:
            app.state.ready = False
            # Do not block process shutdown on timed-out generation tasks:
            # their slots are owned by the tasks themselves and the LLM
            # provider has its own timeout as a backstop.
            executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(
        title="ZGLab Personal Knowledge Assistant",
        version=__version__,
        lifespan=lifespan,
    )

    # CORS configuration
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_credentials=False,  # Never combine wildcard origins with credentials
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # Store runtime and guards in app state for access in routes
    app.state.settings = settings
    app.state.runtime = runtime
    app.state.concurrency_guard = concurrency_guard or ConcurrencyGuard(
        max_concurrent=settings.api_max_concurrent_requests
    )
    app.state.rate_limiter = rate_limiter or RateLimiter(
        max_requests=settings.api_rate_limit_requests,
        window_seconds=settings.api_rate_limit_window_seconds,
    )
    # Thread pool for executing blocking generation calls (app-scoped)
    app.state.executor = executor

    @app.middleware("http")
    async def log_public_request(request: Request, call_next):
        started = perf_counter()
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        except Exception:
            log_http_request(
                logger,
                request_id=request_id,
                path=request.url.path,
                latency_ms=(perf_counter() - started) * 1000,
                status=500,
                error_code=PublicErrorCode.INTERNAL_ERROR.value,
            )
            raise
        error_code = response.headers.get("X-ZGLab-Internal-Error-Code")
        if error_code is not None:
            del response.headers["X-ZGLab-Internal-Error-Code"]
        log_http_request(
            logger,
            request_id=request_id,
            path=request.url.path,
            latency_ms=(perf_counter() - started) * 1000,
            status=response.status_code,
            error_code=error_code,
        )
        return response

    # Request body size limit middleware
    @app.middleware("http")
    async def limit_request_body(request: Request, call_next):
        # Only apply to the public ask endpoints
        if request.method == "POST" and request.url.path in (
            "/api/v1/ask",
            "/api/v1/ask/stream",
        ):
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > settings.api_max_request_body_bytes:
                request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
                return _error_response(
                    request_id,
                    PublicErrorCode.INVALID_REQUEST,
                    "Request body too large",
                    status_code=413,
                )
        return await call_next(request)

    # Exception handlers for consistent error envelope
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        return _error_response(
            request_id,
            PublicErrorCode.INVALID_REQUEST,
            "Invalid request format",
            status_code=422,
        )

    @app.exception_handler(ValidationError)
    async def pydantic_validation_handler(request: Request, exc: ValidationError):
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        return _error_response(
            request_id,
            PublicErrorCode.INVALID_REQUEST,
            "Invalid request format",
            status_code=400,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        # Log the actual error for debugging, but return a safe response
        logger.error("unhandled_api_exception error_code=INTERNAL_ERROR")
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        return _error_response(
            request_id,
            PublicErrorCode.INTERNAL_ERROR,
            "An unexpected error occurred",
            status_code=500,
        )

    # Routes
    @app.get("/health")
    def health() -> dict[str, str]:
        """Lightweight liveness check. Does not load models or call LLM."""
        return {"status": "ok", "version": __version__}

    @app.get("/ready", response_model=None)
    def ready() -> dict[str, str] | JSONResponse:
        """Readiness check: startup, index access, embedding and LLM config passed."""
        if getattr(app.state, "ready", False):
            return {"status": "ready", "version": __version__}
        return JSONResponse(status_code=503, content={"status": "not_ready"})

    @app.get("/sources")
    def list_public_sources() -> list[dict[str, object]]:
        """List public source metadata (sanitized).

        Returns only id, kind, scope, and priority. Does not expose
        local_path, remote URLs, revision, or other internal details.
        """
        settings = app.state.settings
        registry = SourceRegistry.from_yaml(settings.sources_config)
        return [
            {
                "id": source.id,
                "kind": source.kind,
                "scope": source.scope,
                "priority": source.priority,
            }
            for source in registry.public()
        ]

    @app.post("/api/v1/ask", response_model=PublicAskResponse)
    def ask(request: Request, body: PublicAskRequest) -> PublicAskResponse | JSONResponse:
        """Public ask endpoint.

        The request is narrow: only a question is accepted. The server
        enforces public-only retrieval, fixed retrieval mode, and
        server-controlled top_k.
        """
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        request.state.request_id = request_id
        settings = app.state.settings
        runtime = app.state.runtime
        guard: ConcurrencyGuard = app.state.concurrency_guard
        limiter: RateLimiter = app.state.rate_limiter

        # Shared request lifecycle with /api/v1/ask/stream: question length,
        # rate limit and concurrency acquire are identical for both endpoints.
        question, preflight_error = _preflight_controls(
            request, body.question, request_id, settings, limiter, guard
        )
        if preflight_error is not None:
            return preflight_error

        executor: ThreadPoolExecutor = app.state.executor
        try:
            future = executor.submit(_execute_generation, runtime, question)
        except RuntimeError:
            # Executor was shut down (graceful shutdown in progress).
            guard.release()
            return _error_response(
                request_id,
                PublicErrorCode.SERVICE_BUSY,
                "Service is shutting down; please retry later",
                status_code=503,
            )
        # Slot ownership belongs to the generation task itself, not to this
        # HTTP handler: the slot is released only when the task actually
        # completes. An API timeout therefore keeps the slot occupied while
        # the background generation is still running, so new requests get
        # SERVICE_BUSY instead of entering generation on top of it.
        future.add_done_callback(lambda _future: guard.release())

        # Two independent deadline layers:
        # - api_request_timeout_seconds caps the whole workflow (retrieval +
        #   generation + validation); exceeding it returns GENERATION_TIMEOUT.
        # - llm_timeout_seconds caps a single LLM provider call inside the
        #   workflow; exceeding it surfaces as ProviderFailure and returns
        #   PROVIDER_UNAVAILABLE. It is never mapped to INTERNAL_ERROR.
        try:
            result = future.result(timeout=settings.api_request_timeout_seconds)
            response = _map_result_to_response(result, request_id)
        except FuturesTimeoutError:
            logger.warning("request_id=%s error_code=GENERATION_TIMEOUT", request_id)
            return _error_response(
                request_id,
                PublicErrorCode.GENERATION_TIMEOUT,
                "Request timed out; please retry later",
                status_code=504,
            )
        except ProviderFailure:
            logger.warning("request_id=%s error_code=PROVIDER_UNAVAILABLE", request_id)
            return _error_response(
                request_id,
                PublicErrorCode.PROVIDER_UNAVAILABLE,
                "Answer service is temporarily unavailable",
                status_code=503,
            )
        except GenerationError:
            logger.warning("request_id=%s error_code=INTERNAL_ERROR", request_id)
            return _error_response(
                request_id,
                PublicErrorCode.INTERNAL_ERROR,
                "An error occurred while generating the answer",
                status_code=500,
            )
        except Exception:
            # Catch any other exception and return a safe error response
            logger.error("request_id=%s error_code=INTERNAL_ERROR", request_id)
            return _error_response(
                request_id,
                PublicErrorCode.INTERNAL_ERROR,
                "An unexpected error occurred",
                status_code=500,
            )

        return response

    @app.post("/api/v1/ask/stream")
    async def ask_stream(
        request: Request, body: PublicAskRequest
    ) -> Response:
        """Public status-SSE ask endpoint.

        This is status streaming, not raw token streaming: the final answer
        is sent only once, inside the `completed` event, after structured
        generation, citation validation and deterministic rendering.

        Pre-stream failures (INVALID_REQUEST / RATE_LIMITED / SERVICE_BUSY)
        are returned as plain Phase 9A JSON errors before the event stream
        opens. Post-stream failures (GENERATION_TIMEOUT /
        PROVIDER_UNAVAILABLE / INTERNAL_ERROR) are emitted as SSE `error`
        events.
        """
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        request.state.request_id = request_id
        settings = app.state.settings
        runtime = app.state.runtime
        guard: ConcurrencyGuard = app.state.concurrency_guard
        limiter: RateLimiter = app.state.rate_limiter

        # Identical pre-stream lifecycle as /api/v1/ask. Reject with plain
        # JSON errors before opening the event stream.
        question, preflight_error = _preflight_controls(
            request, body.question, request_id, settings, limiter, guard
        )
        if preflight_error is not None:
            return preflight_error

        # Thread -> async bridge. asyncio.Queue is not thread-safe, so the
        # worker thread only touches a stdlib SimpleQueue and wakes the event
        # loop via loop.call_soon_threadsafe.
        loop = asyncio.get_running_loop()
        bridge: queue.SimpleQueue[ProgressStage | object] = queue.SimpleQueue()
        done_sentinel = object()

        def _progress(stage: ProgressStage) -> None:
            # Runs in the generation worker thread.
            loop.call_soon_threadsafe(bridge.put_nowait, stage)

        def _on_done(_future: Future) -> None:
            # Runs in the worker thread when the task really completes.
            loop.call_soon_threadsafe(bridge.put_nowait, done_sentinel)

        executor: ThreadPoolExecutor = app.state.executor
        try:
            future = executor.submit(_execute_generation, runtime, question, _progress)
        except RuntimeError:
            # Executor was shut down (graceful shutdown in progress).
            guard.release()
            return _error_response(
                request_id,
                PublicErrorCode.SERVICE_BUSY,
                "Service is shutting down; please retry later",
                status_code=503,
            )
        # Slot ownership invariant (frozen in Phase 9A): the slot belongs to
        # the generation task, not to the SSE connection. Client disconnect
        # or API timeout never releases it early; only the done callback
        # does. HTTP disconnect != generation cancellation.
        future.add_done_callback(lambda _future: guard.release())
        future.add_done_callback(_on_done)

        return StreamingResponse(
            _stream_events(request, future, bridge, done_sentinel, request_id, settings),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    return app


def _get_client_id(request: Request) -> str:
    """Extract client identity for rate limiting.

    `X-Forwarded-For` is trusted only when the direct peer is an explicitly
    configured reverse proxy. This prevents a public caller from choosing its
    own rate-limit identity.
    """
    peer = request.client.host if request.client else None
    if peer and peer in request.app.state.settings.api_trusted_proxy_ips:
        forwarded_for = request.headers.get("x-forwarded-for", "")
        first = forwarded_for.split(",", maxsplit=1)[0].strip()
        if first:
            return first
    if peer:
        return peer
    return "unknown"


def _preflight_controls(
    request: Request,
    raw_question: str,
    request_id: str,
    settings: Settings,
    limiter: RateLimiter,
    guard: ConcurrencyGuard,
) -> tuple[str, JSONResponse | None]:
    """Shared request lifecycle for /api/v1/ask and /api/v1/ask/stream.

    Runs question length validation, rate limit check and concurrency
    acquire. Both endpoints enforce exactly the same public security
    boundary through this single implementation, so SSE never becomes a
    bypass.

    Returns the normalized question and, when the request must be rejected
    before any generation work starts, the public JSON error response (the
    concurrency slot was not acquired in that case).
    """
    question = raw_question.strip()
    if len(question) < settings.api_question_min_length:
        return question, _error_response(
            request_id,
            PublicErrorCode.INVALID_REQUEST,
            "Question is too short",
            status_code=400,
        )
    if len(question) > settings.api_question_max_length:
        return question, _error_response(
            request_id,
            PublicErrorCode.INVALID_REQUEST,
            f"Question exceeds maximum length of {settings.api_question_max_length} characters",
            status_code=400,
        )

    client_id = _get_client_id(request)
    try:
        limiter.check(client_id)
    except RateLimitExceededError as exc:
        logger.warning("rate_limit_exceeded error_code=RATE_LIMITED")
        return question, _error_response(
            request_id,
            PublicErrorCode.RATE_LIMITED,
            "Rate limit exceeded; please retry later",
            status_code=429,
            retry_after=exc.retry_after_seconds,
        )

    try:
        guard.acquire()
    except ServiceBusyError:
        logger.warning("service_busy error_code=SERVICE_BUSY")
        return question, _error_response(
            request_id,
            PublicErrorCode.SERVICE_BUSY,
            "Service is busy; please retry later",
            status_code=503,
        )
    return question, None


def _execute_generation(
    runtime: ApplicationRuntime | None,
    question: str,
    progress: ProgressCallback | None = None,
) -> GenerationResult:
    """Execute the blocking generation call.

    This function runs in a thread pool to avoid blocking the async event
    loop. The optional progress observer is forwarded to the service; the
    non-stream endpoint always passes None.
    """
    # Tests may inject a runtime; production runtime has already been
    # initialized by the application lifespan before serving requests.
    if runtime is None:
        runtime = _get_runtime()

    with runtime.request_connection() as connection:
        service = runtime.create_service(connection)
        return service.answer(question, progress=progress)


async def _stream_events(
    request: Request,
    future: Future,
    bridge: queue.SimpleQueue,
    done_sentinel: object,
    request_id: str,
    settings: Settings,
) -> AsyncIterator[str]:
    """Yield public SSE events for one generation request.

    Contract:
    - Stage events are narrow `{request_id, stage}`: no evidence content,
      raw LLM text, scores, provider details, token usage or diagnostics.
    - The final validated answer is emitted exactly once, inside
      `completed`, after structured generation + citation validation +
      deterministic rendering.
    - On the API deadline: emit an `error` event (GENERATION_TIMEOUT) and
      close. The background task keeps its concurrency slot until it
      really finishes — timeout never releases the slot.
    - On client disconnect: stop writing silently (no traceback, no error
      leak, no early slot release, no new generation work). HTTP
      disconnect != generation cancellation; cooperative cancellation is
      Post-v1.
    - Heartbeats are SSE comments (`: keep-alive`) only; they never fake
      a processing stage.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.api_request_timeout_seconds
    next_heartbeat = loop.time() + settings.api_sse_heartbeat_seconds

    yield encode_sse_event(
        PublicStreamStage.ACCEPTED.value,
        PublicStreamStatus(request_id=request_id, stage=PublicStreamStage.ACCEPTED),
    )

    async def _next_item() -> ProgressStage | object:
        while True:
            try:
                return bridge.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.01)

    while True:
        now = loop.time()
        if now >= deadline:
            logger.warning("request_id=%s error_code=GENERATION_TIMEOUT", request_id)
            yield _sse_error_event(
                request_id,
                PublicErrorCode.GENERATION_TIMEOUT,
                "Request timed out; please retry later",
            )
            return
        if await request.is_disconnected():
            # Client went away: stop writing events. The generation task
            # continues in the background and releases its own slot when
            # the Future really completes.
            return
        wait_seconds = min(next_heartbeat, deadline) - now
        try:
            item = await asyncio.wait_for(_next_item(), timeout=wait_seconds)
        except TimeoutError:
            if loop.time() >= next_heartbeat:
                yield SSE_HEARTBEAT
                next_heartbeat = loop.time() + settings.api_sse_heartbeat_seconds
            continue
        if item is done_sentinel:
            break
        stage = PublicStreamStage(item.value)  # type: ignore[union-attr]
        yield encode_sse_event(
            stage.value,
            PublicStreamStatus(request_id=request_id, stage=stage),
        )

    # The task really completed. Map the safe result to the terminal event.
    exc = future.exception()
    if exc is not None:
        if isinstance(exc, ProviderFailure):
            logger.warning("request_id=%s error_code=PROVIDER_UNAVAILABLE", request_id)
            yield _sse_error_event(
                request_id,
                PublicErrorCode.PROVIDER_UNAVAILABLE,
                "Answer service is temporarily unavailable",
            )
        elif isinstance(exc, GenerationError):
            logger.warning("request_id=%s error_code=INTERNAL_ERROR", request_id)
            yield _sse_error_event(
                request_id,
                PublicErrorCode.INTERNAL_ERROR,
                "An error occurred while generating the answer",
            )
        else:
            logger.warning("request_id=%s error_code=INTERNAL_ERROR", request_id)
            yield _sse_error_event(
                request_id,
                PublicErrorCode.INTERNAL_ERROR,
                "An unexpected error occurred",
            )
        return

    try:
        completed = _map_result_to_response(future.result(), request_id)
    except ProviderFailure:
        logger.warning("request_id=%s error_code=PROVIDER_UNAVAILABLE", request_id)
        yield _sse_error_event(
            request_id,
            PublicErrorCode.PROVIDER_UNAVAILABLE,
            "Answer service is temporarily unavailable",
        )
        return
    except GenerationError:
        logger.warning("request_id=%s error_code=INTERNAL_ERROR", request_id)
        yield _sse_error_event(
            request_id,
            PublicErrorCode.INTERNAL_ERROR,
            "An error occurred while generating the answer",
        )
        return
    except Exception:
        logger.warning("request_id=%s error_code=INTERNAL_ERROR", request_id)
        yield _sse_error_event(
            request_id,
            PublicErrorCode.INTERNAL_ERROR,
            "An unexpected error occurred",
        )
        return

    yield encode_sse_event(PublicStreamStage.COMPLETED.value, completed)
    logger.info(
        "request_id=%s path=/api/v1/ask/stream status=%s",
        request_id,
        completed.status,
    )


def _sse_error_event(request_id: str, code: PublicErrorCode, message: str) -> str:
    """Encode a terminal SSE error event reusing the Phase 9A envelope.

    Never includes failure_reason, exception names or tracebacks.
    """
    return encode_sse_event(
        "error",
        PublicErrorResponse(
            request_id=request_id,
            error=PublicErrorDetail(code=code, message=message),
        ),
    )


def _map_result_to_response(result: GenerationResult, request_id: str) -> PublicAskResponse:
    """Map internal GenerationResult to public response.

    The mapping strips all internal diagnostics, scores, and paths.
    """
    if result.status == GenerationStatus.FAILED:
        # Map provider failures to appropriate error codes
        if result.failure_reason and "Provider" in result.failure_reason:
            raise ProviderFailure(result.failure_reason)
        raise GenerationError(result.failure_reason or "Generation failed")

    status = (
        PublicStatus.INSUFFICIENT_EVIDENCE
        if result.status == GenerationStatus.INSUFFICIENT_EVIDENCE
        else PublicStatus.ANSWERED
    )

    sources = [
        PublicSource(
            id=source.evidence_id,
            title=source.title,
            section=source.section_path,
            source_path=source.source_path,
        )
        for source in result.answer.sources
    ]

    return PublicAskResponse(
        request_id=request_id,
        status=status,
        answer=result.answer.answer,
        sources=sources,
    )


def _error_response(
    request_id: str,
    code: PublicErrorCode,
    message: str,
    *,
    status_code: int = 400,
    retry_after: float | None = None,
) -> JSONResponse:
    """Create a consistent error response with the public envelope."""
    response = PublicErrorResponse(
        request_id=request_id,
        error=PublicErrorDetail(code=code, message=message),
    )
    headers = {"X-ZGLab-Internal-Error-Code": code.value}
    if retry_after is not None:
        headers["Retry-After"] = str(int(retry_after) + 1)
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(),
        headers=headers,
    )


# Production app instance. The lifespan eagerly initializes this runtime;
# `_get_runtime` only retains a safe fallback for direct internal use.
_runtime: ProductionRuntime | None = None


def _get_runtime() -> ProductionRuntime:
    """Return the production runtime, creating it for direct internal use."""
    global _runtime
    if _runtime is None:
        from zglab_rag.api.runtime import ProductionRuntime
        _runtime = ProductionRuntime.create()
    return _runtime


# Production startup initializes dependencies before the app accepts traffic.
# Tests should use create_app(runtime=fake_runtime) instead.
app = create_app()
