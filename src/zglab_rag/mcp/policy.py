"""Phase 13C — host-side MCP tool policy.

The host allowlist is the single authorization truth. Server annotations
(readOnlyHint/destructiveHint/openWorldHint) are metadata hints only and are
never consulted for authorization. The policy is deliberately thin: no LLM
selection, no role-based tool selection, no planner policy (those are Phase 14
/ Phase 16 concerns).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Frozen Phase 13A/13B allowlist. The host denies anything the server returns
# that is not on this list, and treats a missing expected id as a contract
# mismatch (never silently running half a tool set).
MCP_TOOL_ALLOWLIST: tuple[str, ...] = (
    "json_format",
    "json_minify",
    "json_validate",
    "base64_encode",
    "base64_decode",
    "url_encode",
    "url_decode",
    "text_count",
    "text_deduplicate",
    "timestamp_convert",
)

# Minimal child environment allowlist. Node only needs these to resolve and
# run; secrets (ZGLAB_RAG_* / LLM / search / auth / cookie) never enter it.
_CHILD_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL")


def build_child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build a minimal child environment from an explicit allowlist.

    Never ``os.environ.copy()``: this is the secret-isolation boundary between
    the parent (which may hold LLM/search keys and auth secrets) and the MCP
    child process, which needs none of them.
    """
    env: dict[str, str] = {}
    for key in _CHILD_ENV_ALLOWLIST:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    if extra:
        env.update(extra)
    return env


@dataclass(frozen=True, slots=True)
class MCPToolPolicy:
    allowlist: frozenset[str]
    expected_server_name: str
    max_request_bytes: int
    max_response_bytes: int
    startup_timeout_seconds: float
    call_timeout_seconds: float
    shutdown_timeout_seconds: float

    def is_allowed(self, tool_id: str) -> bool:
        return tool_id in self.allowlist

    @classmethod
    def from_settings(cls, settings) -> MCPToolPolicy:
        return cls(
            allowlist=frozenset(MCP_TOOL_ALLOWLIST),
            expected_server_name=settings.mcp_expected_server_name,
            max_request_bytes=settings.mcp_max_request_bytes,
            max_response_bytes=settings.mcp_max_response_bytes,
            startup_timeout_seconds=settings.mcp_startup_timeout_seconds,
            call_timeout_seconds=settings.mcp_call_timeout_seconds,
            shutdown_timeout_seconds=settings.mcp_shutdown_timeout_seconds,
        )
