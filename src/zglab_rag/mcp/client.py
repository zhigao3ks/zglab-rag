"""Phase 13C — thin wrapper over the official Python MCP client.

This is the ONLY place that touches the official SDK's stdio transport and
``ClientSession``. Everything above it (``MCPToolRuntime``) depends on the
narrow ``MCPConnection`` protocol so unit tests can inject a fake without a
real Node subprocess.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from zglab_rag.mcp.contracts import MCPToolDescriptor, RawToolOutcome, ServerIdentity


class MCPConnection(Protocol):
    """Narrow session seam used by MCPToolRuntime (real SDK or fake in tests)."""

    async def initialize(self) -> ServerIdentity: ...

    async def list_tools(self) -> list[MCPToolDescriptor]: ...

    async def call_tool(self, name: str, arguments: dict) -> RawToolOutcome: ...

    async def aclose(self) -> None: ...


MCPConnectionFactory = Callable[[StdioServerParameters], Awaitable[MCPConnection]]


def _first_text(content: object) -> str | None:
    for block in content or []:
        if getattr(block, "type", None) == "text":
            return getattr(block, "text", None)
    return None


def _annotations(tool: object) -> dict:
    raw = getattr(tool, "annotations", None)
    if raw is None:
        return {}
    if hasattr(raw, "model_dump"):
        return raw.model_dump(exclude_none=True)
    return dict(raw)


class _StdioMCPConnection:
    """A single live stdio connection (spawn + initialize + session)."""

    def __init__(self, params: StdioServerParameters) -> None:
        self._params = params
        self._stdio_ctx = None
        self._session_ctx = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> _StdioMCPConnection:
        try:
            self._stdio_ctx = stdio_client(self._params)
            read, write = await self._stdio_ctx.__aenter__()
            self._session_ctx = ClientSession(read, write)
            self._session = await self._session_ctx.__aenter__()
        except BaseException:
            await self.aclose()
            raise
        return self

    async def initialize(self) -> ServerIdentity:
        assert self._session is not None
        result = await self._session.initialize()
        info = result.server_info
        return ServerIdentity(
            name=info.name,
            version=info.version,
            protocol_version=result.protocol_version,
        )

    async def list_tools(self) -> list[MCPToolDescriptor]:
        assert self._session is not None
        result = await self._session.list_tools()
        return [
            MCPToolDescriptor(
                id=tool.name,
                title=tool.title,
                description=tool.description,
                input_schema=dict(tool.input_schema),
                annotations=_annotations(tool),
            )
            for tool in result.tools
        ]

    async def call_tool(self, name: str, arguments: dict) -> RawToolOutcome:
        assert self._session is not None
        result = await self._session.call_tool(name, arguments)
        return RawToolOutcome(
            structured_content=result.structured_content,
            is_error=bool(result.is_error),
            content_text=_first_text(result.content),
        )

    async def aclose(self) -> None:
        if self._session_ctx is not None:
            await self._session_ctx.__aexit__(None, None, None)
            self._session_ctx = None
            self._session = None
        if self._stdio_ctx is not None:
            await self._stdio_ctx.__aexit__(None, None, None)
            self._stdio_ctx = None


async def open_mcp_connection(params: StdioServerParameters) -> MCPConnection:
    """Spawn the server and return a live connection (caller owns aclose())."""
    connection = _StdioMCPConnection(params)
    await connection.__aenter__()
    return connection
