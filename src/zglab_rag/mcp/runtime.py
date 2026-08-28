"""Phase 13C — MCPToolRuntime: the Python host facade.

Owns one long-lived MCP server child process, lazily started on first use.
Enforces, in order: the kill switch, the host allowlist, the host input-size
bound, the catalog membership, the hard call deadline, and the host output-size
bound. Transport failures mark the session unhealthy and close the child; the
next call lazily reconnects. Concurrency is serialized by a semaphore.

This runtime never answers questions and never selects tools — that is Phase 14.
"""

from __future__ import annotations

import asyncio
import json
import logging

from mcp import StdioServerParameters

from zglab_rag.config import Settings
from zglab_rag.mcp.client import MCPConnection, MCPConnectionFactory, open_mcp_connection
from zglab_rag.mcp.contracts import (
    MCPToolDescriptor,
    MCPToolResult,
    MCPToolRuntimeState,
    RawToolOutcome,
    ServerIdentity,
    ToolCatalogSnapshot,
)
from zglab_rag.mcp.errors import MCPError, MCPErrorCode, map_server_error_code
from zglab_rag.mcp.policy import MCP_TOOL_ALLOWLIST, MCPToolPolicy, build_child_env

logger = logging.getLogger(__name__)


def _measure(value: object) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


class MCPToolRuntime:
    def __init__(
        self,
        settings: Settings,
        policy: MCPToolPolicy,
        *,
        connection_factory: MCPConnectionFactory = open_mcp_connection,
    ) -> None:
        self._settings = settings
        self._policy = policy
        self._connection_factory = connection_factory
        self._concurrency = asyncio.Semaphore(max(1, settings.mcp_max_concurrent_calls))
        self._connection: MCPConnection | None = None
        self._catalog: ToolCatalogSnapshot | None = None
        self._state = (
            MCPToolRuntimeState.DISABLED
            if not settings.mcp_enabled
            else MCPToolRuntimeState.NOT_STARTED
        )

    # ------------------------------------------------------------------ state

    @property
    def enabled(self) -> bool:
        return self._settings.mcp_enabled

    @property
    def state(self) -> MCPToolRuntimeState:
        return self._state

    # -------------------------------------------------------------- lifecycle

    async def __aenter__(self) -> MCPToolRuntime:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        async with self._concurrency:
            await self._close_connection()
            if self.enabled:
                self._state = MCPToolRuntimeState.CLOSED

    # ------------------------------------------------------------------- API

    async def list_tools(self) -> ToolCatalogSnapshot:
        """Return the validated host-allowlist catalog; raises MCPError on setup failure."""
        if not self.enabled:
            raise MCPError(MCPErrorCode.MCP_DISABLED, "MCP tool runtime is disabled")
        async with self._concurrency:
            await self._ensure_ready()
            assert self._catalog is not None
            return self._catalog

    async def call_tool(self, tool_id: str, arguments: dict) -> MCPToolResult:
        """Call one allowlisted tool; always returns an MCPToolResult."""
        if not self.enabled:
            return self._error(tool_id, MCPErrorCode.MCP_DISABLED, "MCP tool runtime is disabled")
        if not self._policy.is_allowed(tool_id):
            return self._error(
                tool_id, MCPErrorCode.MCP_TOOL_NOT_ALLOWED, f"tool '{tool_id}' is not allowed"
            )
        if _measure(arguments) > self._policy.max_request_bytes:
            return self._error(
                tool_id, MCPErrorCode.MCP_INVALID_INPUT, "arguments exceed the host request limit"
            )

        async with self._concurrency:
            try:
                await self._ensure_ready()
            except MCPError as exc:
                return self._error(tool_id, exc.code, exc.message)

            assert self._connection is not None and self._catalog is not None
            if tool_id not in self._catalog.tool_ids():
                return self._error(
                    tool_id, MCPErrorCode.MCP_TOOL_NOT_FOUND, f"tool '{tool_id}' not in catalog"
                )

            try:
                async with asyncio.timeout(self._policy.call_timeout_seconds):
                    outcome = await self._connection.call_tool(tool_id, arguments)
            except TimeoutError:
                await self._mark_unhealthy()
                return self._error(
                    tool_id,
                    MCPErrorCode.MCP_CALL_TIMEOUT,
                    f"tool call exceeded {self._policy.call_timeout_seconds:.1f}s",
                )
            except asyncio.CancelledError:
                await self._mark_unhealthy()
                raise
            except Exception:
                logger.warning("mcp_call_transport_failed tool_id=%s", tool_id, exc_info=True)
                await self._mark_unhealthy()
                return self._error(
                    tool_id, MCPErrorCode.MCP_PROCESS_EXITED, "MCP server connection was lost"
                )

        return self._to_result(tool_id, outcome)

    # ---------------------------------------------------------------- connect

    def _server_params(self) -> StdioServerParameters:
        return StdioServerParameters(
            command=self._settings.mcp_server_command,
            args=list(self._settings.mcp_server_args),
            env=build_child_env(),
            cwd=self._settings.mcp_server_cwd,
        )

    async def _ensure_ready(self) -> None:
        if self._state == MCPToolRuntimeState.READY and self._connection is not None:
            return
        await self._close_connection()
        connection: MCPConnection | None = None
        try:
            async with asyncio.timeout(self._policy.startup_timeout_seconds):
                connection = await self._connection_factory(self._server_params())
                identity = await connection.initialize()
                descriptors = await connection.list_tools()
            self._validate_identity(identity)
            catalog = self._validate_catalog(identity, descriptors)
        except BaseException as exc:
            if connection is not None:
                await connection.aclose()
            self._state = MCPToolRuntimeState.UNHEALTHY
            raise self._map_start_failure(exc) from exc
        self._connection = connection
        self._catalog = catalog
        self._state = MCPToolRuntimeState.READY

    def _validate_identity(self, identity: ServerIdentity) -> None:
        name = identity.name
        expected = self._policy.expected_server_name
        if name != expected:
            raise MCPError(
                MCPErrorCode.MCP_CONTRACT_MISMATCH,
                f"unexpected MCP server name {name!r} (expected {expected!r})",
            )

    def _validate_catalog(
        self, identity: ServerIdentity, descriptors: list[MCPToolDescriptor]
    ) -> ToolCatalogSnapshot:
        by_id = {descriptor.id: descriptor for descriptor in descriptors}
        allowed: list[MCPToolDescriptor] = []
        for tool_id in MCP_TOOL_ALLOWLIST:
            descriptor = by_id.get(tool_id)
            if descriptor is None:
                raise MCPError(
                    MCPErrorCode.MCP_CONTRACT_MISMATCH,
                    f"MCP server is missing expected tool '{tool_id}'",
                )
            schema = descriptor.input_schema
            if not isinstance(schema, dict) or schema.get("type") != "object":
                raise MCPError(
                    MCPErrorCode.MCP_CONTRACT_MISMATCH,
                    f"tool '{tool_id}' has no object input schema",
                )
            allowed.append(descriptor)
        return ToolCatalogSnapshot(identity=identity, tools=tuple(allowed))

    def _map_start_failure(self, exc: BaseException) -> MCPError:
        if isinstance(exc, MCPError):
            return exc
        if isinstance(exc, TimeoutError):
            return MCPError(
                MCPErrorCode.MCP_START_FAILED,
                f"MCP server startup exceeded {self._policy.startup_timeout_seconds:.1f}s",
            )
        if isinstance(exc, (OSError, ValueError)):
            return MCPError(MCPErrorCode.MCP_START_FAILED, f"could not start MCP server: {exc}")
        return MCPError(MCPErrorCode.MCP_HANDSHAKE_FAILED, f"MCP handshake failed: {exc}")

    # ------------------------------------------------------------- teardown

    async def _mark_unhealthy(self) -> None:
        self._state = MCPToolRuntimeState.UNHEALTHY
        await self._close_connection()

    async def _close_connection(self) -> None:
        connection, self._connection = self._connection, None
        self._catalog = None
        if connection is None:
            return
        try:
            async with asyncio.timeout(self._policy.shutdown_timeout_seconds):
                await connection.aclose()
        except Exception:
            logger.warning("mcp_connection_close_failed", exc_info=True)

    # ------------------------------------------------------------- mapping

    def _to_result(self, tool_id: str, outcome: RawToolOutcome) -> MCPToolResult:
        if outcome.is_error:
            structured = outcome.structured_content
            sc = structured if isinstance(structured, dict) else {}
            code = map_server_error_code(sc.get("code"))
            message = sc.get("message")
            if not isinstance(message, str):
                message = outcome.content_text or "tool execution failed"
            details = sc.get("details") if isinstance(sc.get("details"), dict) else None
            return MCPToolResult(
                status="error",
                tool_id=tool_id,
                error_code=code.value,
                error_message=message,
                details=details,
            )
        structured = outcome.structured_content
        if not isinstance(structured, dict):
            return self._error(
                tool_id,
                MCPErrorCode.MCP_PROTOCOL_ERROR,
                "tool result is missing structured content",
            )
        if structured.get("status") != "success":
            return self._error(
                tool_id, MCPErrorCode.MCP_PROTOCOL_ERROR, "unexpected structured content status"
            )
        output = structured.get("result")
        if _measure(output) > self._policy.max_response_bytes:
            return self._error(
                tool_id, MCPErrorCode.MCP_OUTPUT_TOO_LARGE, "tool output exceeds the host limit"
            )
        return MCPToolResult(status="success", tool_id=tool_id, output=output)

    @staticmethod
    def _error(tool_id: str, code: MCPErrorCode, message: str) -> MCPToolResult:
        return MCPToolResult(
            status="error", tool_id=tool_id, error_code=code.value, error_message=message
        )


def build_mcp_tool_runtime(
    settings: Settings, *, connection_factory: MCPConnectionFactory = open_mcp_connection
) -> MCPToolRuntime:
    """Build the runtime without spawning anything (lazy connect on first use)."""
    return MCPToolRuntime(
        settings,
        MCPToolPolicy.from_settings(settings),
        connection_factory=connection_factory,
    )
