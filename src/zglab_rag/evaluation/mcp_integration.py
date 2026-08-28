"""Phase 13C — cross-language MCP integration harness.

A real Python official MCP client spawns the compiled Node MCP server over
stdio and exercises initialize → tools/list → tools/call → close. This is the
Definition of Done for the cross-repo boundary; it accepts no HTTP/user input.

Usage:
    uv run python -m zglab_rag.evaluation.mcp_integration \
        --command node --args dist-mcp/cli.js --cwd /path/to/zglab-tools
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from zglab_rag.config import Settings
from zglab_rag.mcp.runtime import MCPToolRuntime, build_mcp_tool_runtime


def _result_summary(result) -> dict:
    if result.status == "success":
        return {"status": "success", "output": result.output}
    return {"status": "error", "code": result.error_code, "message": result.error_message}


async def _run(args) -> dict:
    settings = Settings(
        mcp_enabled=True,
        mcp_server_command=args.command,
        mcp_server_args=args.args,
        mcp_server_cwd=args.cwd,
        mcp_call_timeout_seconds=args.call_timeout,
    )
    runtime: MCPToolRuntime = build_mcp_tool_runtime(settings)
    report: dict = {
        "evaluation": "mcp-client-integration",
        "phase": "13C",
        "generated_at": datetime.now(UTC).isoformat(),
        "command": args.command,
        "args": args.args,
        "cwd": args.cwd,
    }
    try:
        started = time.perf_counter()
        snapshot = await runtime.list_tools()
        report["startup_ms"] = round((time.perf_counter() - started) * 1000, 3)
        report["server_name"] = snapshot.identity.name
        report["server_version"] = snapshot.identity.version
        report["protocol_version"] = snapshot.identity.protocol_version
        report["tool_count"] = len(snapshot.tools)
        report["tool_ids"] = [tool.id for tool in snapshot.tools]

        calls: dict[str, dict] = {}
        started = time.perf_counter()
        calls["json_format"] = await runtime.call_tool("json_format", {"text": '{"b":1,"a":2}'})
        calls["json_validate_invalid"] = await runtime.call_tool(
            "json_validate", {"text": '{"a":}'}
        )
        calls["base64_encode"] = await runtime.call_tool("base64_encode", {"text": "hello 世界 🌍"})
        encoded = calls["base64_encode"].output
        calls["base64_decode"] = await runtime.call_tool("base64_decode", {"text": encoded})
        calls["url_encode"] = await runtime.call_tool("url_encode", {"text": "a=b&c 中文"})
        url_encoded = calls["url_encode"].output
        calls["url_decode"] = await runtime.call_tool("url_decode", {"text": url_encoded})
        calls["text_count"] = await runtime.call_tool("text_count", {"text": "中文abc"})
        calls["text_deduplicate"] = await runtime.call_tool(
            "text_deduplicate", {"text": "b\na\nb\nc"}
        )
        calls["timestamp_seconds"] = await runtime.call_tool(
            "timestamp_convert", {"timestamp": "1700000000"}
        )
        calls["timestamp_milliseconds"] = await runtime.call_tool(
            "timestamp_convert", {"timestamp": "1700000000000"}
        )
        report["calls_ms"] = round((time.perf_counter() - started) * 1000, 3)
        report["calls"] = {key: _result_summary(value) for key, value in calls.items()}
        report["success"] = all(value.status == "success" for value in calls.values())
    finally:
        await runtime.close()
    report["clean_shutdown"] = runtime.state.value == "closed"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Cross-language MCP integration harness")
    parser.add_argument("--command", default="node", help="MCP server executable")
    parser.add_argument("--args", nargs="*", default=["dist-mcp/cli.js"], help="MCP server argv")
    parser.add_argument("--cwd", required=True, help="MCP server working directory (zglab-tools)")
    parser.add_argument("--call-timeout", type=float, default=5.0, help="per-call deadline (s)")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    parsed = parser.parse_args()

    report = asyncio.run(_run(parsed))

    parsed.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    output_path = parsed.output_dir / f"mcp-client-integration-{stamp}.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"server={report['server_name']}@{report['server_version']}")
    print(f"protocol_version={report['protocol_version']}")
    print(f"tool_count={report['tool_count']}")
    print(f"startup_ms={report['startup_ms']}")
    print(f"calls_ms={report['calls_ms']}")
    print(f"success={report['success']}")
    print(f"clean_shutdown={report['clean_shutdown']}")
    print(f"report={output_path}")
    return 0 if report["success"] and report["clean_shutdown"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
