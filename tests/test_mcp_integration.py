"""Phase 13C — opt-in cross-language integration (Python client -> Node server).

Skipped by default so CI never needs the sibling zglab-tools repo. Enable with:
    ZGLAB_RAG_MCP_INTEGRATION=1
    ZGLAB_RAG_MCP_SERVER_CWD=/path/to/zglab-tools
    ZGLAB_RAG_MCP_SERVER_COMMAND=node            # optional
    ZGLAB_RAG_MCP_SERVER_ARGS='["dist-mcp/cli.js"]'  # optional
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from zglab_rag.config import Settings
from zglab_rag.mcp.policy import MCP_TOOL_ALLOWLIST
from zglab_rag.mcp.runtime import build_mcp_tool_runtime

pytestmark = pytest.mark.skipif(
    os.environ.get("ZGLAB_RAG_MCP_INTEGRATION") != "1",
    reason="set ZGLAB_RAG_MCP_INTEGRATION=1 (and MCP_SERVER_CWD) to run the cross-repo test",
)


def _settings() -> Settings:
    cwd = os.environ["ZGLAB_RAG_MCP_SERVER_CWD"]
    command = os.environ.get("ZGLAB_RAG_MCP_SERVER_COMMAND", "node")
    args = json.loads(os.environ.get("ZGLAB_RAG_MCP_SERVER_ARGS", '["dist-mcp/cli.js"]'))
    return Settings(
        mcp_enabled=True,
        mcp_server_command=command,
        mcp_server_args=args,
        mcp_server_cwd=cwd,
        mcp_call_timeout_seconds=5.0,
    )


def test_cross_language_integration() -> None:
    async def scenario() -> None:
        runtime = build_mcp_tool_runtime(_settings())
        try:
            snapshot = await runtime.list_tools()
            assert snapshot.identity.name == "zglab-tools-mcp"
            assert snapshot.identity.protocol_version
            assert [t.id for t in snapshot.tools] == list(MCP_TOOL_ALLOWLIST)

            result = await runtime.call_tool("json_format", {"text": '{"b":1,"a":2}'})
            assert result.status == "success"
            assert result.output == '{\n  "b": 1,\n  "a": 2\n}'

            result = await runtime.call_tool("json_validate", {"text": '{"a":}'})
            assert result.status == "success"
            assert result.output["valid"] is False

            encoded = await runtime.call_tool("base64_encode", {"text": "hello 世界 🌍"})
            assert encoded.status == "success"
            decoded = await runtime.call_tool("base64_decode", {"text": encoded.output})
            assert decoded.output == "hello 世界 🌍"

            url_encoded = await runtime.call_tool("url_encode", {"text": "a=b&c 中文"})
            url_decoded = await runtime.call_tool("url_decode", {"text": url_encoded.output})
            assert url_decoded.output == "a=b&c 中文"

            counted = await runtime.call_tool("text_count", {"text": "中文abc"})
            assert counted.output["characterCount"] == 5

            deduped = await runtime.call_tool("text_deduplicate", {"text": "b\na\nb\nc"})
            assert deduped.output["output"] == "b\na\nc"

            ts = await runtime.call_tool("timestamp_convert", {"timestamp": "1700000000"})
            assert ts.output["iso"] == "2023-11-14T22:13:20.000Z"
        finally:
            await runtime.close()

    asyncio.run(scenario())
