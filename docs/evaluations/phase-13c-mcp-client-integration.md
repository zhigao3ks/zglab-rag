# Phase 13C — MCP Client + Capability Integration 验收

> 范围：仅 Phase 13C（Python Host → official Python MCP client → stdio → Node MCP server）。
> 真实跨语言数据，非伪造。

## 1. 环境

- 客户端：官方 `mcp` Python SDK **2.1.1**（`mcp>=2,<3`，uv.lock 锁定）。
- 服务端：`zglab-tools` `node dist-mcp/cli.js`（`npm run build:mcp` 编译产物，SDK
  `@modelcontextprotocol/sdk` 1.30.0）。
- spawn：`node dist-mcp/cli.js`，`cwd=/home/zhigao/projects/zglab-tools`，`shell=False`，
  `env=build_child_env()`（最小 allowlist，无 secret）。

## 2. 真实 handshake / catalog

```text
server          = zglab-tools-mcp @ 0.0.1
protocol_version = 2025-11-25（双方 SDK 协商，非硬编码）
tool_count      = 10（与冻结 allowlist 完全一致）
startup_ms      ≈ 231（spawn + initialize + tools/list）
```

## 3. 真实 tools/call

全部通过（`calls_ms ≈ 117`）：

| tool | 断言 |
| --- | --- |
| `json_format` | `{"b":1,"a":2}` → 2 空格格式化、键序保留 |
| `json_validate` | 非法 JSON → `{valid:false}`（成功结果，非错误） |
| `base64_encode`/`base64_decode` | `hello 世界 🌍` UTF-8 往返 |
| `url_encode`/`url_decode` | `a=b&c 中文` Unicode 往返 |
| `text_count` | `中文abc` → `characterCount=5` |
| `text_deduplicate` | `b\na\nb\nc` → `b\na\nc` |
| `timestamp_convert` | 秒/毫秒 → UTC ISO |

`clean_shutdown = true`（client close → child 干净退出）。

## 4. 单测 / 契约测试（默认 pytest，不依赖 sibling repo）

`tests/test_mcp_runtime.py`（18 个，fake `MCPConnection` 注入）覆盖：

- kill switch（disabled）、host allowlist（发送前拒绝、server call 计数 0）；
- catalog：恰好 10 个、丢弃 server 额外工具、缺 expected tool → `MCP_CONTRACT_MISMATCH`、
  server name 不匹配 → `MCP_CONTRACT_MISMATCH`；
- 输入 bound（发送前拒绝）、输出 bound（`MCP_OUTPUT_TOO_LARGE`）；
- 结构化结果：success、缺失/错误 status → `MCP_PROTOCOL_ERROR`；
- server 错误码映射（已知 → 对应 host 码，未知 → `MCP_INTERNAL_ERROR`）；
- call 超时 → `MCP_CALL_TIMEOUT` + UNHEALTHY + 关闭旧连接 + lazy 重连；
- 进程退出 → `MCP_PROCESS_EXITED` + UNHEALTHY；
- 懒连接（使用前不 spawn）、`close()` → CLOSED；
- secret 隔离：`build_child_env` 不含 `ZGLAB_RAG_*` secret；真实子进程 fixture 证明 secret 不在
  child 环境。

## 5. 跨语言 integration test（opt-in）

`tests/test_mcp_integration.py`：默认 `skip`；`ZGLAB_RAG_MCP_INTEGRATION=1` +
`ZGLAB_RAG_MCP_SERVER_CWD=<zglab-tools>` 时执行并 **1 passed**。

## 6. 回归

- `uv run pytest -q`：**532 passed, 1 skipped**（新增 18 个 mcp 单测 + 1 个 opt-in integration）。
- `uv run ruff check .`：All checks passed。
- `/api/v2/ask`、`/api/v2/ask/stream` 契约未改动（无 `tool/mcp/agent` 行为）。
- `zglab-tools`：`format:check` / `lint` / `check` / `test`（156）/ `build` / `build:mcp` /
  `test:mcp`（26）全部通过；浏览器 `dist/` 不含 MCP / `node:*`。

## 7. 状态

```text
Phase 13A ✅   Phase 13B ✅   Phase 13C ✅   Phase 13D ⏳（未开始）
```

不标 `Phase 13 COMPLETE`，不标 `PRODUCTION ACCEPTED`。
