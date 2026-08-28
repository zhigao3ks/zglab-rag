"""Phase 13C — machine-facing MCP tool contracts (Python host side).

These are the typed models the Python host uses to reason about the MCP tool
runtime. They deliberately mirror neither the Phase 12 Capability/Generation
models (an MCP tool result is NOT a grounded answer) nor the raw MCP protocol
models (the protocol JSON Schema stays an opaque ``dict``). This keeps
``Tool Observation`` concept-isolated until Phase 14 introduces a unified
``AgentObservation``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MCPToolRuntimeState(StrEnum):
    DISABLED = "disabled"
    NOT_STARTED = "not_started"
    READY = "ready"
    UNHEALTHY = "unhealthy"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class ServerIdentity:
    name: str
    version: str
    protocol_version: str


@dataclass(frozen=True, slots=True)
class MCPToolDescriptor:
    id: str
    title: str | None
    description: str | None
    input_schema: dict
    annotations: dict


@dataclass(frozen=True, slots=True)
class ToolCatalogSnapshot:
    identity: ServerIdentity
    tools: tuple[MCPToolDescriptor, ...]

    def tool_ids(self) -> frozenset[str]:
        return frozenset(tool.id for tool in self.tools)


@dataclass(frozen=True, slots=True)
class MCPToolResult:
    """Uniform outcome of a single host tool call.

    ``status`` is "success" or "error". A tool execution error (``is_error``)
    is folded into a result with a stable ``error_code``, never raised as an
    arbitrary exception; transport/process failures are reported the same way
    so the future Agent sees one uniform shape.
    """

    status: str
    tool_id: str
    output: object | None = None
    error_code: str | None = None
    error_message: str | None = None
    details: dict | None = None


@dataclass(frozen=True, slots=True)
class RawToolOutcome:
    """Normalized raw tools/call outcome from the SDK (not yet policy-checked)."""

    structured_content: object | None
    is_error: bool
    content_text: str | None
