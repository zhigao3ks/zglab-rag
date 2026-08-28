"""Phase 13C — MCP tool runtime unit/contract tests (no sibling repo, no Node).

All transport behaviour is injected via a fake MCPConnection factory, so the
default pytest suite never requires zglab-tools or a Node executable.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from dataclasses import replace

import pytest

from zglab_rag.config import Settings
from zglab_rag.mcp.contracts import (
    MCPToolDescriptor,
    MCPToolRuntimeState,
    RawToolOutcome,
    ServerIdentity,
)
from zglab_rag.mcp.errors import MCPError, MCPErrorCode
from zglab_rag.mcp.policy import MCP_TOOL_ALLOWLIST, MCPToolPolicy, build_child_env
from zglab_rag.mcp.runtime import MCPToolRuntime

IDENTITY = ServerIdentity(name="zglab-tools-mcp", version="0.0.1", protocol_version="2025-06-18")


def descriptor(tool_id: str) -> MCPToolDescriptor:
    return MCPToolDescriptor(
        id=tool_id,
        title=tool_id,
        description=f"test {tool_id}",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        annotations={"read_only_hint": True, "destructive_hint": False},
    )


def standard_tools() -> list[MCPToolDescriptor]:
    return [descriptor(tool_id) for tool_id in MCP_TOOL_ALLOWLIST]


class FakeConnection:
    def __init__(
        self,
        *,
        identity: ServerIdentity = IDENTITY,
        tools: list[MCPToolDescriptor] | None = None,
        hang: bool = False,
        raise_on_call: BaseException | None = None,
        on_call=None,
    ) -> None:
        self.identity = identity
        self.tools = tools if tools is not None else standard_tools()
        self.hang = hang
        self.raise_on_call = raise_on_call
        self.on_call = on_call
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    async def initialize(self) -> ServerIdentity:
        return self.identity

    async def list_tools(self) -> list[MCPToolDescriptor]:
        return self.tools

    async def call_tool(self, name: str, arguments: dict) -> RawToolOutcome:
        self.calls.append((name, arguments))
        if self.hang:
            await asyncio.sleep(3600)
        if self.raise_on_call is not None:
            raise self.raise_on_call
        if self.on_call is not None:
            return self.on_call(name, arguments)
        if name == "json_validate":
            return RawToolOutcome(
                structured_content={"status": "success", "result": {"valid": True}},
                is_error=False,
                content_text=None,
            )
        return RawToolOutcome(
            structured_content={"status": "success", "result": "ok"},
            is_error=False,
            content_text='"ok"',
        )

    async def aclose(self) -> None:
        self.closed = True


def make_settings(**overrides) -> Settings:
    return Settings(mcp_enabled=True, **overrides)


def run(coro):
    return asyncio.run(coro)


def build_runtime(
    settings: Settings, connections: list[FakeConnection]
) -> tuple[MCPToolRuntime, list[FakeConnection]]:
    used: list[FakeConnection] = []

    def factory(_params):
        async def _make() -> FakeConnection:
            connection = connections.pop(0)
            used.append(connection)
            return connection

        return _make()

    policy = MCPToolPolicy.from_settings(settings)
    return MCPToolRuntime(settings, policy, connection_factory=factory), used


async def _expect(code: MCPErrorCode, coro) -> None:
    with pytest.raises(MCPError) as exc_info:
        await coro
    assert exc_info.value.code == code


# ---------------------------------------------------------------------------


def test_disabled_runtime_never_connects() -> None:
    settings = Settings(mcp_enabled=False)
    runtime = MCPToolRuntime(settings, MCPToolPolicy.from_settings(settings))

    run(_expect(MCPErrorCode.MCP_DISABLED, runtime.list_tools()))
    result = run(runtime.call_tool("json_format", {"text": "{}"}))
    assert result.status == "error"
    assert result.error_code == MCPErrorCode.MCP_DISABLED.value


def test_host_allowlist_rejects_before_send() -> None:
    spawned: list[bool] = []

    def factory(_params):
        async def _make() -> FakeConnection:
            spawned.append(True)
            return FakeConnection()

        return _make()

    settings = make_settings()
    runtime = MCPToolRuntime(
        settings, MCPToolPolicy.from_settings(settings), connection_factory=factory
    )
    result = run(runtime.call_tool("shell_exec", {"cmd": "id"}))
    assert result.error_code == MCPErrorCode.MCP_TOOL_NOT_ALLOWED.value
    assert spawned == []


def test_catalog_exposes_only_allowlist_and_drops_extras() -> None:
    tools = standard_tools() + [descriptor("shell_exec"), descriptor("github_push")]
    runtime, _ = build_runtime(make_settings(), [FakeConnection(tools=tools)])
    snapshot = run(runtime.list_tools())
    assert snapshot.tool_ids() == frozenset(MCP_TOOL_ALLOWLIST)
    assert [t.id for t in snapshot.tools] == list(MCP_TOOL_ALLOWLIST)


def test_missing_expected_tool_is_contract_mismatch() -> None:
    tools = [descriptor(t) for t in MCP_TOOL_ALLOWLIST if t != "json_format"]
    runtime, _ = build_runtime(make_settings(), [FakeConnection(tools=tools)])
    run(_expect(MCPErrorCode.MCP_CONTRACT_MISMATCH, runtime.list_tools()))


def test_unexpected_server_name_is_contract_mismatch() -> None:
    runtime, _ = build_runtime(
        make_settings(), [FakeConnection(identity=replace(IDENTITY, name="evil-server"))]
    )
    run(_expect(MCPErrorCode.MCP_CONTRACT_MISMATCH, runtime.list_tools()))


def test_host_input_size_rejected_before_send() -> None:
    connections = [FakeConnection()]
    runtime, _ = build_runtime(make_settings(mcp_max_request_bytes=16), connections)
    result = run(runtime.call_tool("json_format", {"text": "x" * 100}))
    assert result.error_code == MCPErrorCode.MCP_INVALID_INPUT.value
    assert connections[0].calls == []


def test_large_output_is_bounded() -> None:
    def on_call(_name, _arguments) -> RawToolOutcome:
        return RawToolOutcome(
            structured_content={"status": "success", "result": "x" * 1000},
            is_error=False,
            content_text=None,
        )

    runtime, _ = build_runtime(
        make_settings(mcp_max_response_bytes=32), [FakeConnection(on_call=on_call)]
    )
    result = run(runtime.call_tool("json_format", {"text": "{}"}))
    assert result.error_code == MCPErrorCode.MCP_OUTPUT_TOO_LARGE.value


def test_success_structured_result() -> None:
    runtime, _ = build_runtime(make_settings(), [FakeConnection()])
    result = run(runtime.call_tool("json_validate", {"text": "{}"}))
    assert result.status == "success"
    assert result.output == {"valid": True}


def test_missing_structured_content_is_protocol_error() -> None:
    def on_call(_name, _arguments) -> RawToolOutcome:
        return RawToolOutcome(structured_content=None, is_error=False, content_text="x")

    runtime, _ = build_runtime(make_settings(), [FakeConnection(on_call=on_call)])
    result = run(runtime.call_tool("json_format", {"text": "{}"}))
    assert result.error_code == MCPErrorCode.MCP_PROTOCOL_ERROR.value


def test_unexpected_status_is_protocol_error() -> None:
    def on_call(_name, _arguments) -> RawToolOutcome:
        return RawToolOutcome(
            structured_content={"status": "weird"}, is_error=False, content_text=None
        )

    runtime, _ = build_runtime(make_settings(), [FakeConnection(on_call=on_call)])
    result = run(runtime.call_tool("json_format", {"text": "{}"}))
    assert result.error_code == MCPErrorCode.MCP_PROTOCOL_ERROR.value


def test_server_error_known_code_is_mapped() -> None:
    def on_call(_name, _arguments) -> RawToolOutcome:
        return RawToolOutcome(
            structured_content={"status": "error", "code": "INVALID_INPUT", "message": "bad"},
            is_error=True,
            content_text="INVALID_INPUT: bad",
        )

    runtime, _ = build_runtime(make_settings(), [FakeConnection(on_call=on_call)])
    result = run(runtime.call_tool("json_format", {"text": "{}"}))
    assert result.status == "error"
    assert result.error_code == MCPErrorCode.MCP_INVALID_INPUT.value
    assert result.error_message == "bad"


def test_server_error_unknown_code_maps_to_internal() -> None:
    def on_call(_name, _arguments) -> RawToolOutcome:
        return RawToolOutcome(
            structured_content={"status": "error", "code": "SOMETHING_NEW", "message": "x"},
            is_error=True,
            content_text="x",
        )

    runtime, _ = build_runtime(make_settings(), [FakeConnection(on_call=on_call)])
    result = run(runtime.call_tool("json_format", {"text": "{}"}))
    assert result.error_code == MCPErrorCode.MCP_INTERNAL_ERROR.value


def test_call_timeout_marks_unhealthy_and_reconnects() -> None:
    connections = [FakeConnection(hang=True), FakeConnection()]
    runtime, used = build_runtime(make_settings(mcp_call_timeout_seconds=0.05), connections)

    first = run(runtime.call_tool("json_format", {"text": "{}"}))
    assert first.error_code == MCPErrorCode.MCP_CALL_TIMEOUT.value
    assert runtime.state == MCPToolRuntimeState.UNHEALTHY
    assert used[0].closed is True

    second = run(runtime.call_tool("json_format", {"text": "{}"}))
    assert second.status == "success"
    assert runtime.state == MCPToolRuntimeState.READY
    assert used[1].closed is False


def test_process_exit_marks_unhealthy() -> None:
    runtime, used = build_runtime(
        make_settings(), [FakeConnection(raise_on_call=RuntimeError("boom"))]
    )
    result = run(runtime.call_tool("json_format", {"text": "{}"}))
    assert result.error_code == MCPErrorCode.MCP_PROCESS_EXITED.value
    assert runtime.state == MCPToolRuntimeState.UNHEALTHY
    assert used[0].closed is True


def test_lazy_connect_no_spawn_until_use() -> None:
    spawned: list[bool] = []

    def factory(_params):
        async def _make() -> FakeConnection:
            spawned.append(True)
            return FakeConnection()

        return _make()

    settings = make_settings()
    runtime = MCPToolRuntime(
        settings, MCPToolPolicy.from_settings(settings), connection_factory=factory
    )
    assert spawned == []
    assert runtime.state == MCPToolRuntimeState.NOT_STARTED
    run(runtime.list_tools())
    assert spawned == [True]


def test_close_sets_closed_state() -> None:
    runtime, _ = build_runtime(make_settings(), [FakeConnection()])
    run(runtime.list_tools())
    assert runtime.state == MCPToolRuntimeState.READY
    run(runtime.close())
    assert runtime.state == MCPToolRuntimeState.CLOSED


def test_build_child_env_excludes_secrets() -> None:
    os.environ["ZGLAB_RAG_TEST_SECRET"] = "should-not-leak"
    os.environ["ZGLAB_RAG_LLM_API_KEY"] = "secret-key"
    os.environ["ZGLAB_RAG_SEARCH_API_KEY"] = "search-key"
    try:
        env = build_child_env()
        assert "ZGLAB_RAG_TEST_SECRET" not in env
        assert "ZGLAB_RAG_LLM_API_KEY" not in env
        assert "ZGLAB_RAG_SEARCH_API_KEY" not in env
    finally:
        os.environ.pop("ZGLAB_RAG_TEST_SECRET", None)
        os.environ.pop("ZGLAB_RAG_LLM_API_KEY", None)
        os.environ.pop("ZGLAB_RAG_SEARCH_API_KEY", None)


def test_child_process_does_not_receive_secret() -> None:
    # Real child fixture (system Python only, no Node/sibling repo): prove the
    # constructed child environment never carries a parent secret.
    os.environ["ZGLAB_RAG_TEST_SECRET"] = "should-not-leak"
    try:
        child_env = build_child_env()
        script = (
            "import os,sys;"
            "sys.stdout.write('PRESENT' if 'ZGLAB_RAG_TEST_SECRET' in os.environ else 'ABSENT')"
        )
        result = subprocess.run(
            [sys.executable, "-c", script], env=child_env, capture_output=True, text=True
        )
        assert result.stdout.strip() == "ABSENT"
    finally:
        os.environ.pop("ZGLAB_RAG_TEST_SECRET", None)
