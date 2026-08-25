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
    AuthActivateRequest,
    AuthChangePasswordRequest,
    AuthLoginRequest,
    AuthResultResponse,
    AuthSessionResponse,
    AuthUserPublic,
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
from zglab_rag.api.security import (
    AuthRuntime,
    get_client_id,
    session_cookie_kwargs,
    verify_state_change_origin,
)
from zglab_rag.api.sse import SSE_HEADERS, SSE_HEARTBEAT, encode_sse_event
from zglab_rag.auth.audit import AuditLogger
from zglab_rag.auth.errors import (
    AccountUnavailableError,
    CsrfError,
    InvalidCredentialsError,
    LoginThrottledError,
    OriginError,
    PasswordPolicyError,
    QuotaExceededError,
    SessionError,
    TokenError,
)
from zglab_rag.auth.models import AuditEvent, AuthenticatedPrincipal
from zglab_rag.auth.quota import UsageGuard
from zglab_rag.auth.session import csrf_token_for
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
    auth_runtime: AuthRuntime | None = None,
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
                validate_production_security_settings(settings)
                app.state.runtime = _get_runtime()
                app.state.runtime.verify_ready()
                # Phase 11: authentication is the foundation of every
                # cost-bearing capability, so an unavailable auth.db means
                # the service is not ready.
                app.state.auth_runtime.verify_ready()
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
    app.state.auth_runtime = auth_runtime or AuthRuntime.from_settings(settings)
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
        # Only apply to the cost-bearing / credential-carrying endpoints
        if request.method == "POST" and request.url.path in (
            "/api/v1/ask",
            "/api/v1/ask/stream",
            "/api/v2/ask",
            "/api/v2/ask/stream",
            "/api/v2/auth/login",
            "/api/v2/auth/activate",
            "/api/v2/auth/change-password",
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

        # Phase 11 v1 retirement: the frozen anonymous contract must stop
        # being an anonymous LLM consumption entry once production migrates.
        retired = _v1_retirement_response(settings, request_id)
        if retired is not None:
            return retired
        if not settings.llm_enabled:
            return _error_response(
                request_id,
                PublicErrorCode.SERVICE_DISABLED,
                "The answer service is currently disabled",
                status_code=503,
            )

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

        # Two independent deadline layers; shared with /api/v2/ask.
        return _collect_generation(settings, future, request_id)

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
        guard: ConcurrencyGuard = app.state.concurrency_guard
        limiter: RateLimiter = app.state.rate_limiter

        # Identical retirement / kill-switch / pre-stream lifecycle as
        # /api/v1/ask; SSE never becomes a bypass of either policy.
        retired = _v1_retirement_response(settings, request_id)
        if retired is not None:
            return retired
        if not settings.llm_enabled:
            return _error_response(
                request_id,
                PublicErrorCode.SERVICE_DISABLED,
                "The answer service is currently disabled",
                status_code=503,
            )

        # Identical pre-stream lifecycle as /api/v1/ask. Reject with plain
        # JSON errors before opening the event stream.
        question, preflight_error = _preflight_controls(
            request, body.question, request_id, settings, limiter, guard
        )
        if preflight_error is not None:
            return preflight_error

        return await _open_ask_stream(app, request, question, request_id, settings)

    # ------------------------------------------------------------------
    # Phase 11: authenticated API v2
    # ------------------------------------------------------------------

    def _auth_cookie_token(request: Request) -> str | None:
        return request.cookies.get(app.state.settings.auth_cookie_name)

    @app.post("/api/v2/auth/login")
    async def auth_login(request: Request, body: AuthLoginRequest):
        """Authenticate with username + password and open a server session.

        Security order: Origin validation -> login throttle -> credential
        check. All credential failures return the same public error so
        account state is never enumerable.
        """
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        settings = app.state.settings
        auth_runtime: AuthRuntime = app.state.auth_runtime
        try:
            verify_state_change_origin(request, settings)
        except OriginError:
            return _error_response(
                request_id,
                PublicErrorCode.CSRF_REJECTED,
                "Request origin rejected",
                status_code=403,
            )
        client_id = get_client_id(request)
        try:
            auth_runtime.throttle.check_and_record(ip=client_id, username=body.username)
        except LoginThrottledError as exc:
            return _error_response(
                request_id,
                PublicErrorCode.RATE_LIMITED,
                "Too many login attempts; please retry later",
                status_code=429,
                retry_after=exc.retry_after_seconds,
            )

        def _login():
            with auth_runtime.connection() as connection:
                service = auth_runtime.session_service(connection, settings)
                return service.login(
                    body.username,
                    body.password,
                    client_hint=client_id,
                    request_id=request_id,
                )

        try:
            result = await asyncio.to_thread(_login)
        except InvalidCredentialsError:
            return _error_response(
                request_id,
                PublicErrorCode.INVALID_CREDENTIALS,
                "Invalid username or password",
                status_code=401,
            )
        response = JSONResponse(
            content=AuthSessionResponse(
                request_id=request_id,
                user=AuthUserPublic(
                    username=result.principal.username,
                    role=result.principal.role.value,
                ),
                csrf_token=result.csrf_token,
            ).model_dump()
        )
        # Host-only cookie: no Domain attribute; the plaintext session
        # token never appears in any response body or log.
        response.set_cookie(
            settings.auth_cookie_name, result.session_token, **session_cookie_kwargs(settings)
        )
        return response

    @app.get("/api/v2/auth/me")
    def auth_me(request: Request):
        """Restore the frontend auth state from the HttpOnly cookie."""
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        settings = app.state.settings
        auth_runtime: AuthRuntime = app.state.auth_runtime
        try:
            with auth_runtime.connection() as connection:
                service = auth_runtime.session_service(connection, settings)
                principal, session = service.resolve_session(_auth_cookie_token(request))
                csrf_token = csrf_token_for(session)
        except SessionError:
            return _error_response(
                request_id,
                PublicErrorCode.AUTHENTICATION_REQUIRED,
                "Authentication required",
                status_code=401,
            )
        return AuthSessionResponse(
            request_id=request_id,
            user=AuthUserPublic(username=principal.username, role=principal.role.value),
            csrf_token=csrf_token,
        )

    @app.post("/api/v2/auth/logout")
    async def auth_logout(request: Request):
        """Revoke the server-side session immediately and clear the cookie.

        Idempotent: an already-invalid session still clears the cookie and
        reports success. CSRF is enforced only while a valid session exists.
        """
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        settings = app.state.settings
        auth_runtime: AuthRuntime = app.state.auth_runtime
        try:
            verify_state_change_origin(request, settings)
        except OriginError:
            return _error_response(
                request_id,
                PublicErrorCode.CSRF_REJECTED,
                "Request origin rejected",
                status_code=403,
            )
        cookie_token = _auth_cookie_token(request)
        csrf_header = request.headers.get("x-csrf-token")

        def _logout() -> None:
            with auth_runtime.connection() as connection:
                service = auth_runtime.session_service(connection, settings)
                try:
                    _principal, session = service.resolve_session(cookie_token)
                except SessionError:
                    return  # Already gone; logout stays idempotent.
                service.verify_csrf(session, csrf_header)
                service.logout(cookie_token, request_id=request_id)

        try:
            await asyncio.to_thread(_logout)
        except CsrfError:
            return _error_response(
                request_id,
                PublicErrorCode.CSRF_REJECTED,
                "CSRF validation failed",
                status_code=403,
            )
        response = JSONResponse(
            content=AuthResultResponse(request_id=request_id, result="logged_out").model_dump()
        )
        response.delete_cookie(settings.auth_cookie_name, path="/")
        return response

    async def _consume_credential_token(
        request: Request, body: AuthActivateRequest, kind: str
    ):
        """Shared flow for the two purpose-pinned token endpoints.

        ``kind`` selects the ONLY accepted token purpose: "activate" calls
        activate_account (ACTIVATE_ACCOUNT tokens), "reset" calls
        reset_password_with_token (RESET_PASSWORD tokens). There is no
        public endpoint that inspects a token and auto-dispatches the
        credential operation; cross-purpose tokens are rejected.
        """
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        settings = app.state.settings
        auth_runtime: AuthRuntime = app.state.auth_runtime
        try:
            verify_state_change_origin(request, settings)
        except OriginError:
            return _error_response(
                request_id,
                PublicErrorCode.CSRF_REJECTED,
                "Request origin rejected",
                status_code=403,
            )

        def _consume():
            with auth_runtime.connection() as connection:
                service = auth_runtime.identity_service(connection, settings)
                if kind == "activate":
                    return service.activate_account(
                        body.token, body.password, request_id=request_id
                    )
                return service.reset_password_with_token(
                    body.token, body.password, request_id=request_id
                )

        try:
            await asyncio.to_thread(_consume)
        except TokenError:
            return _error_response(
                request_id,
                PublicErrorCode.INVALID_REQUEST,
                "The link is invalid or has expired",
                status_code=400,
            )
        except PasswordPolicyError:
            return _error_response(
                request_id,
                PublicErrorCode.INVALID_REQUEST,
                "Password does not satisfy the password policy",
                status_code=400,
            )
        except AccountUnavailableError:
            return _error_response(
                request_id,
                PublicErrorCode.ACCOUNT_UNAVAILABLE,
                "Account is unavailable",
                status_code=403,
            )
        result_label = "account_activated" if kind == "activate" else "password_updated"
        return AuthResultResponse(request_id=request_id, result=result_label)

    @app.post("/api/v2/auth/activate")
    async def auth_activate(request: Request, body: AuthActivateRequest):
        """Consume a single-use ACTIVATE_ACCOUNT token only.

        A RESET_PASSWORD token submitted here is rejected (purpose
        boundary). The admin never learns the chosen password.
        """
        return await _consume_credential_token(request, body, "activate")

    @app.post("/api/v2/auth/reset-password")
    async def auth_reset_password(request: Request, body: AuthActivateRequest):
        """Consume a single-use RESET_PASSWORD token only.

        An ACTIVATE_ACCOUNT token submitted here is rejected (purpose
        boundary). The old password was already invalidated when the
        reset token was issued.
        """
        return await _consume_credential_token(request, body, "reset")

    @app.post("/api/v2/auth/change-password")
    async def auth_change_password(request: Request, body: AuthChangePasswordRequest):
        """Change the authenticated user's password.

        The current session survives; every other session is revoked.
        """
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        settings = app.state.settings
        auth_runtime: AuthRuntime = app.state.auth_runtime
        try:
            verify_state_change_origin(request, settings)
        except OriginError:
            return _error_response(
                request_id,
                PublicErrorCode.CSRF_REJECTED,
                "Request origin rejected",
                status_code=403,
            )
        cookie_token = _auth_cookie_token(request)
        csrf_header = request.headers.get("x-csrf-token")

        def _change():
            with auth_runtime.connection() as connection:
                service = auth_runtime.session_service(connection, settings)
                _principal, session = service.resolve_session(cookie_token)
                service.verify_csrf(session, csrf_header)
                return service.change_password(
                    cookie_token,
                    body.current_password,
                    body.new_password,
                    min_length=settings.auth_password_min_length,
                    max_length=settings.auth_password_max_length,
                    request_id=request_id,
                )

        try:
            await asyncio.to_thread(_change)
        except SessionError:
            return _error_response(
                request_id,
                PublicErrorCode.AUTHENTICATION_REQUIRED,
                "Authentication required",
                status_code=401,
            )
        except CsrfError:
            return _error_response(
                request_id,
                PublicErrorCode.CSRF_REJECTED,
                "CSRF validation failed",
                status_code=403,
            )
        except InvalidCredentialsError:
            return _error_response(
                request_id,
                PublicErrorCode.INVALID_CREDENTIALS,
                "Current password is incorrect",
                status_code=401,
            )
        except PasswordPolicyError:
            return _error_response(
                request_id,
                PublicErrorCode.INVALID_REQUEST,
                "Password does not satisfy the password policy",
                status_code=400,
            )
        return AuthResultResponse(request_id=request_id, result="password_changed")

    def _v2_security_gate(
        cookie_token: str | None,
        csrf_header: str | None,
    ) -> AuthenticatedPrincipal:
        """Shared AuthN -> AuthZ -> CSRF gate for v2 ask endpoints.

        One auth.db connection serves the whole gate so SSE and plain ask
        enforce the exact same boundary (no SSE bypass). Authorization is
        implicit and default-deny: resolve_session only accepts ACTIVE
        accounts. Quota is intentionally NOT recorded here: it is counted
        only once a request is about to enter the cost-bearing workflow.
        """
        auth_runtime: AuthRuntime = app.state.auth_runtime
        with auth_runtime.connection() as connection:
            service = auth_runtime.session_service(connection, settings)
            principal, session = service.resolve_session(cookie_token)
            service.verify_csrf(session, csrf_header)
        return principal

    def _v2_quota_gate(principal: AuthenticatedPrincipal, client_id: str, request_id: str) -> None:
        """Atomic per-user quota check; audits and re-raises on denial."""
        auth_runtime: AuthRuntime = app.state.auth_runtime
        with auth_runtime.connection() as connection:
            usage_guard = UsageGuard(connection, auth_runtime.quota_config(settings))
            try:
                usage_guard.check_and_record(principal.user_id)
            except QuotaExceededError:
                AuditLogger(connection).record(
                    AuditEvent.QUOTA_EXCEEDED,
                    result="denied",
                    user_id=principal.user_id,
                    request_id=request_id,
                    client_hint=client_id,
                )
                raise

    async def _v2_ask_preflight(
        request: Request,
    ) -> AuthenticatedPrincipal | JSONResponse:
        """Security-boundary preflight for v2 ask endpoints.

        Hardened precedence: Origin -> Authentication -> Authorization ->
        CSRF first, then capability policy (kill switch). Anonymous callers
        therefore ALWAYS receive AUTHENTICATION_REQUIRED, regardless of the
        LLM kill switch state; capability enabled/disabled state is never
        disclosed to unauthenticated callers.

        Returns the authenticated principal, or the rejection response.
        Runs before question processing, concurrency and quota, so
        rejected requests consume zero generation resources and no quota.
        """
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        try:
            verify_state_change_origin(request, settings)
        except OriginError:
            return _error_response(
                request_id,
                PublicErrorCode.CSRF_REJECTED,
                "Request origin rejected",
                status_code=403,
            )
        client_id = get_client_id(request)
        try:
            principal = await asyncio.to_thread(
                _v2_security_gate,
                _auth_cookie_token(request),
                request.headers.get("x-csrf-token"),
            )
        except SessionError:
            return _error_response(
                request_id,
                PublicErrorCode.AUTHENTICATION_REQUIRED,
                "Authentication required",
                status_code=401,
            )
        except CsrfError:
            return _error_response(
                request_id,
                PublicErrorCode.CSRF_REJECTED,
                "CSRF validation failed",
                status_code=403,
            )
        # Capability policy is evaluated only after the security boundary:
        # the kill switch must never leak its state to anonymous callers.
        if not settings.llm_enabled:
            return _error_response(
                request_id,
                PublicErrorCode.SERVICE_DISABLED,
                "The answer service is currently disabled",
                status_code=503,
            )
        request.state.v2_client_id = client_id
        return principal

    def _v2_quota_then_error(
        principal: AuthenticatedPrincipal,
        guard: ConcurrencyGuard,
        request_id: str,
        client_id: str,
    ) -> JSONResponse | None:
        """Record quota after the concurrency slot is held.

        SERVICE_BUSY rejections happen before this step and never consume
        quota; a quota-exceeded request releases the slot and does not
        count itself (the atomic transaction rolls back).
        """
        try:
            _v2_quota_gate(principal, client_id, request_id)
        except QuotaExceededError as exc:
            guard.release()
            return _error_response(
                request_id,
                PublicErrorCode.QUOTA_EXCEEDED,
                "Usage quota exceeded; please retry later",
                status_code=429,
                retry_after=exc.retry_after_seconds,
            )
        return None

    @app.post("/api/v2/ask", response_model=PublicAskResponse)
    async def ask_v2(request: Request, body: PublicAskRequest) -> PublicAskResponse | JSONResponse:
        """Authenticated ask endpoint.

        Security order: Validation -> Origin -> Authentication ->
        Authorization -> CSRF -> capability policy -> question controls ->
        Concurrency -> Quota -> GroundedAnswerService. Only requests that
        really enter generation consume quota. Retrieval stays public-only
        downstream.
        """
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        request.state.request_id = request_id
        guard: ConcurrencyGuard = app.state.concurrency_guard

        preflight = await _v2_ask_preflight(request)
        if isinstance(preflight, JSONResponse):
            return preflight
        principal = preflight

        question = body.question.strip()
        length_error = _question_length_error(question, settings, request_id)
        if length_error is not None:
            return length_error

        try:
            guard.acquire()
        except ServiceBusyError:
            logger.warning("service_busy error_code=SERVICE_BUSY")
            return _error_response(
                request_id,
                PublicErrorCode.SERVICE_BUSY,
                "Service is busy; please retry later",
                status_code=503,
            )

        quota_error = _v2_quota_then_error(
            principal, guard, request_id, getattr(request.state, "v2_client_id", "unknown")
        )
        if quota_error is not None:
            return quota_error

        executor: ThreadPoolExecutor = app.state.executor
        try:
            future = executor.submit(_execute_generation, app.state.runtime, question)
        except RuntimeError:
            # Shutdown race: refund the quota, the work never started.
            _refund_v2_quota(principal)
            guard.release()
            return _error_response(
                request_id,
                PublicErrorCode.SERVICE_BUSY,
                "Service is shutting down; please retry later",
                status_code=503,
            )
        future.add_done_callback(lambda _future: guard.release())
        return _collect_generation(settings, future, request_id)

    @app.post("/api/v2/ask/stream")
    async def ask_stream_v2(request: Request, body: PublicAskRequest) -> Response:
        """Authenticated status-SSE endpoint.

        Shares the exact same security gate as /api/v2/ask: authentication,
        CSRF and quota are all enforced before the event stream opens, so
        SSE never becomes a bypass. Pre-stream rejections are plain JSON.
        """
        request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
        request.state.request_id = request_id
        guard: ConcurrencyGuard = app.state.concurrency_guard

        preflight = await _v2_ask_preflight(request)
        if isinstance(preflight, JSONResponse):
            return preflight
        principal = preflight

        question = body.question.strip()
        length_error = _question_length_error(question, settings, request_id)
        if length_error is not None:
            return length_error

        try:
            guard.acquire()
        except ServiceBusyError:
            logger.warning("service_busy error_code=SERVICE_BUSY")
            return _error_response(
                request_id,
                PublicErrorCode.SERVICE_BUSY,
                "Service is busy; please retry later",
                status_code=503,
            )

        quota_error = _v2_quota_then_error(
            principal, guard, request_id, getattr(request.state, "v2_client_id", "unknown")
        )
        if quota_error is not None:
            return quota_error

        def _refund_quota() -> None:
            _refund_v2_quota(principal)

        return await _open_ask_stream(
            app, request, question, request_id, settings, on_submit_failure=_refund_quota
        )

    def _refund_v2_quota(principal: AuthenticatedPrincipal) -> None:
        auth_runtime: AuthRuntime = app.state.auth_runtime
        try:
            with auth_runtime.connection() as connection:
                UsageGuard(connection, auth_runtime.quota_config(settings)).refund(
                    principal.user_id
                )
        except Exception:
            logger.warning("quota_refund_failed user_id=%s", principal.user_id)

    return app


def _get_client_id(request: Request) -> str:
    """Extract client identity for rate limiting (Phase 9 compatibility)."""
    return get_client_id(request)


def validate_production_security_settings(settings: Settings) -> None:
    """Fail-closed startup validation for production security posture.

    Production must never serve the anonymous v1 ask endpoints: forgetting
    ZGLAB_RAG_API_V1_RETIRED would silently leave an anonymous LLM
    consumption entry after Phase 11. So a production process refuses to
    start unless retirement is explicitly enabled. Local regression keeps
    the historical v1 behavior under env=development.
    """
    if settings.env == "production" and not settings.api_v1_retired:
        raise RuntimeError(
            "Refusing to start: production requires ZGLAB_RAG_API_V1_RETIRED=true "
            "so /api/v1/ask cannot remain an anonymous LLM consumption entry"
        )


def _v1_retirement_response(settings: Settings, request_id: str) -> JSONResponse | None:
    """Return the 410 Gone response when the anonymous v1 API is retired.

    The Phase 9 v1 contract stays historically frozen in docs/public-api.md;
    this switch is flipped during the Phase 11 production migration so v1
    can never remain an anonymous LLM consumption entry.
    """
    if not settings.api_v1_retired:
        return None
    return _error_response(
        request_id,
        PublicErrorCode.API_RETIRED,
        "This API version has been retired; please use the authenticated application",
        status_code=410,
    )


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
    length_error = _question_length_error(question, settings, request_id)
    if length_error is not None:
        return question, length_error

    client_id = get_client_id(request)
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


def _question_length_error(
    question: str, settings: Settings, request_id: str
) -> JSONResponse | None:
    """Shared question length validation for v1 and v2 ask endpoints."""
    if len(question) < settings.api_question_min_length:
        return _error_response(
            request_id,
            PublicErrorCode.INVALID_REQUEST,
            "Question is too short",
            status_code=400,
        )
    if len(question) > settings.api_question_max_length:
        return _error_response(
            request_id,
            PublicErrorCode.INVALID_REQUEST,
            f"Question exceeds maximum length of {settings.api_question_max_length} characters",
            status_code=400,
        )
    return None


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


def _collect_generation(
    settings: Settings, future: Future, request_id: str
) -> PublicAskResponse | JSONResponse:
    """Await a submitted generation task and map it to the public envelope.

    Two independent deadline layers:
    - api_request_timeout_seconds caps the whole workflow (retrieval +
      generation + validation); exceeding it returns GENERATION_TIMEOUT.
    - llm_timeout_seconds caps a single LLM provider call inside the
      workflow; exceeding it surfaces as ProviderFailure and returns
      PROVIDER_UNAVAILABLE. It is never mapped to INTERNAL_ERROR.
    """
    try:
        result = future.result(timeout=settings.api_request_timeout_seconds)
        return _map_result_to_response(result, request_id)
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


async def _open_ask_stream(
    app: FastAPI,
    request: Request,
    question: str,
    request_id: str,
    settings: Settings,
    *,
    on_submit_failure: callable | None = None,
) -> Response:
    """Submit generation and open the status SSE stream.

    Shared by /api/v1/ask/stream and /api/v2/ask/stream; both endpoints
    must run their security gates before calling this helper. When the
    executor rejects the submission (graceful shutdown race), the optional
    on_submit_failure hook runs first — v2 uses it to refund quota that
    was already counted for work that never started.
    """
    runtime = app.state.runtime
    guard: ConcurrencyGuard = app.state.concurrency_guard

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
        if on_submit_failure is not None:
            on_submit_failure()
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
