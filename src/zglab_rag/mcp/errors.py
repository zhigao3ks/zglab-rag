"""Phase 13C — host-side MCP error model.

Host-level failures (setup / policy / transport / limits) use one small typed
error code set. Tool business errors are NOT exceptions: they come back as
``MCPToolResult(status="error", error_code=...)`` with the server's safe code
validated and mapped into this same namespace. Unknown server codes never
become an internal exception type.
"""

from __future__ import annotations

from enum import StrEnum


class MCPErrorCode(StrEnum):
    MCP_DISABLED = "MCP_DISABLED"
    MCP_START_FAILED = "MCP_START_FAILED"
    MCP_HANDSHAKE_FAILED = "MCP_HANDSHAKE_FAILED"
    MCP_CONTRACT_MISMATCH = "MCP_CONTRACT_MISMATCH"
    MCP_TOOL_NOT_ALLOWED = "MCP_TOOL_NOT_ALLOWED"
    MCP_TOOL_NOT_FOUND = "MCP_TOOL_NOT_FOUND"
    MCP_INVALID_INPUT = "MCP_INVALID_INPUT"
    MCP_CALL_TIMEOUT = "MCP_CALL_TIMEOUT"
    MCP_PROTOCOL_ERROR = "MCP_PROTOCOL_ERROR"
    MCP_OUTPUT_TOO_LARGE = "MCP_OUTPUT_TOO_LARGE"
    MCP_PROCESS_EXITED = "MCP_PROCESS_EXITED"
    MCP_INTERNAL_ERROR = "MCP_INTERNAL_ERROR"


class MCPError(Exception):
    """A host-side MCP failure with a stable code and a safe message."""

    def __init__(self, code: MCPErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# The Phase 13A tool error codes the server may return; anything else is not
# trusted as an internal type.
_KNOWN_SERVER_CODES = frozenset(
    {
        "INVALID_INPUT",
        "INPUT_TOO_LARGE",
        "OUTPUT_TOO_LARGE",
        "UNSUPPORTED_OPTION",
        "EXECUTION_TIMEOUT",
        "TOOL_NOT_FOUND",
        "TOOL_DISABLED",
        "INTERNAL_TOOL_ERROR",
    }
)

_SERVER_TO_HOST: dict[str, MCPErrorCode] = {
    "INVALID_INPUT": MCPErrorCode.MCP_INVALID_INPUT,
    "INPUT_TOO_LARGE": MCPErrorCode.MCP_INVALID_INPUT,
    "UNSUPPORTED_OPTION": MCPErrorCode.MCP_INVALID_INPUT,
    "OUTPUT_TOO_LARGE": MCPErrorCode.MCP_OUTPUT_TOO_LARGE,
    "EXECUTION_TIMEOUT": MCPErrorCode.MCP_CALL_TIMEOUT,
    "TOOL_NOT_FOUND": MCPErrorCode.MCP_TOOL_NOT_FOUND,
    "TOOL_DISABLED": MCPErrorCode.MCP_TOOL_NOT_ALLOWED,
    "INTERNAL_TOOL_ERROR": MCPErrorCode.MCP_INTERNAL_ERROR,
}


def map_server_error_code(raw: object | None) -> MCPErrorCode:
    """Validate and map a server tool error code; unknown -> INTERNAL_ERROR."""
    if isinstance(raw, str) and raw in _KNOWN_SERVER_CODES:
        return _SERVER_TO_HOST[raw]
    return MCPErrorCode.MCP_INTERNAL_ERROR
