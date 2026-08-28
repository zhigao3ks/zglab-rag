"""MCP Tool Runtime (Phase 13C).

The Python host facade that spawns the zglab-tools MCP server over stdio and
calls the frozen allowlist of deterministic tools. Deliberately separate from
the Phase 12 Capability layer: an MCP tool result is a Tool Observation, not a
grounded answer or evidence.
"""

from zglab_rag.mcp.contracts import (
    MCPToolDescriptor,
    MCPToolResult,
    MCPToolRuntimeState,
    ServerIdentity,
    ToolCatalogSnapshot,
)
from zglab_rag.mcp.errors import MCPError, MCPErrorCode
from zglab_rag.mcp.policy import MCP_TOOL_ALLOWLIST, MCPToolPolicy, build_child_env
from zglab_rag.mcp.runtime import MCPToolRuntime, build_mcp_tool_runtime

__all__ = [
    "MCPError",
    "MCPErrorCode",
    "MCP_TOOL_ALLOWLIST",
    "MCPToolDescriptor",
    "MCPToolPolicy",
    "MCPToolResult",
    "MCPToolRuntime",
    "MCPToolRuntimeState",
    "ServerIdentity",
    "ToolCatalogSnapshot",
    "build_child_env",
    "build_mcp_tool_runtime",
]
