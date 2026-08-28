"""Phase 13D real-child lifecycle tests (Python host -> Node stdio process).

They are opt-in because the repository deliberately does not vendor the
zglab-tools test fixture.  Unlike the unit tests, these assertions observe the
PID emitted by a real Node child and prove it has been reaped before reconnect.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from zglab_rag.config import Settings
from zglab_rag.mcp.errors import MCPErrorCode
from zglab_rag.mcp.runtime import build_mcp_tool_runtime

pytestmark = pytest.mark.skipif(
    os.environ.get("ZGLAB_RAG_MCP_INTEGRATION") != "1",
    reason="set ZGLAB_RAG_MCP_INTEGRATION=1 and MCP_SERVER_CWD to run real Node lifecycle tests",
)


async def _pid(path: Path, occurrence: int) -> int:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if path.exists():
            pids = [int(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
            if len(pids) >= occurrence:
                return pids[occurrence - 1]
        await asyncio.sleep(0.01)
    raise AssertionError("Node fixture did not report its PID")


def _dead(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


async def _wait_dead(pid: int) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if _dead(pid):
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"Node child PID {pid} survived lifecycle cleanup")


def _settings(pid_file: Path, *, call_timeout: float = 0.15) -> Settings:
    cwd = os.environ["ZGLAB_RAG_MCP_SERVER_CWD"]
    return Settings(
        mcp_enabled=True,
        mcp_server_command=os.environ.get("ZGLAB_RAG_MCP_SERVER_COMMAND", "node"),
        mcp_server_args=["scripts/mcp-hanging-test-server.mjs", str(pid_file)],
        mcp_server_cwd=cwd,
        mcp_call_timeout_seconds=call_timeout,
        mcp_shutdown_timeout_seconds=5.0,
    )


def test_real_child_timeout_reaps_pid_and_reconnects(tmp_path: Path) -> None:
    async def scenario() -> None:
        pid_file = tmp_path / "pids"
        runtime = build_mcp_tool_runtime(_settings(pid_file))
        try:
            first = await runtime.call_tool("json_format", {"text": "__HANG__"})
            first_pid = await _pid(pid_file, 1)
            assert first.error_code == MCPErrorCode.MCP_CALL_TIMEOUT.value
            await _wait_dead(first_pid)

            second = await runtime.call_tool("json_format", {"text": "ok"})
            second_pid = await _pid(pid_file, 2)
            assert second.status == "success"
            assert second.output == "ok"
            assert second_pid != first_pid
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_real_child_cancellation_and_exit_reap_pids(tmp_path: Path) -> None:
    async def scenario() -> None:
        pid_file = tmp_path / "pids"
        runtime = build_mcp_tool_runtime(_settings(pid_file, call_timeout=5.0))
        try:
            pending = asyncio.create_task(runtime.call_tool("json_format", {"text": "__HANG__"}))
            first_pid = await _pid(pid_file, 1)
            # The child reports its PID before the MCP handshake. Give the
            # real request time to reach the intentionally hanging handler.
            await asyncio.sleep(0.1)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            await _wait_dead(first_pid)

            exited = await runtime.call_tool("json_format", {"text": "__EXIT__"})
            second_pid = await _pid(pid_file, 2)
            assert exited.error_code == MCPErrorCode.MCP_PROCESS_EXITED.value
            await _wait_dead(second_pid)

            recovered = await runtime.call_tool("json_format", {"text": "ok"})
            assert recovered.status == "success"
        finally:
            await runtime.close()

    asyncio.run(scenario())


def test_real_node_child_does_not_inherit_test_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("ZGLAB_RAG_SEARCH_API_KEY", "test-secret")
    monkeypatch.setenv("ZGLAB_RAG_TEST_SECRET", "test-secret")

    async def scenario() -> None:
        runtime = build_mcp_tool_runtime(_settings(tmp_path / "pids"))
        try:
            result = await runtime.call_tool("json_format", {"text": "__ENV__"})
            assert result.status == "success"
            assert result.output == {"isolated": True}
        finally:
            await runtime.close()

    asyncio.run(scenario())
