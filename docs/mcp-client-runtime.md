# MCP Client Runtime（Phase 13C）

本文描述 `zglab-rag` 作为 **MCP Host** 的 Python 侧运行时：通过官方 Python MCP SDK 以 stdio
启动并调用 `zglab-tools` MCP Server，形成一个 bounded、typed、host-policy-enforced 的 MCP
Tool Runtime。本阶段不改动 `/api/v2/ask`，不接入任何公网/用户入口。

> 权威 Phase 路线见 `docs/roadmap-v2.md`。13C 只落地 Host + Client，不实现 Agent Planner
> （14）、不实现生产部署/审计（13D）。

## 1. 架构

```text
zglab-rag
    ↓
MCPToolRuntime（src/zglab_rag/mcp/runtime.py）
    ↓
Official Python MCP Client（mcp，src/zglab_rag/mcp/client.py）
    ↓ stdio
Node subprocess
    ↓
zglab-tools MCP Server（node dist-mcp/cli.js）
    ↓
ToolRegistry → Shared Tool Core
```

Python 不 import、不复制 TypeScript tool logic；MCP 是正式的跨语言 process boundary。

## 2. 官方 Python SDK

- package：`mcp`，**2.1.1**（MIT，`requires-python >=3.10`，本项目 3.12），`pyproject.toml`
  约束 `mcp>=2,<3`，由 `uv.lock` 锁定。
- 使用 `mcp.client.stdio.stdio_client` + `mcp.ClientSession` + `mcp.StdioServerParameters`。
- `StdioServerParameters.env` 只“合并到安全默认环境之上”：官方 transport 默认只继承
  `HOME/LOGNAME/PATH/SHELL/TERM/USER`（POSIX），因此 secret 隔离由 SDK 默认行为 + 本项目的
  最小 env allowlist 双重保证。

## 3. 模块（`src/zglab_rag/mcp/`）

| 文件 | 职责 |
| --- | --- |
| `contracts.py` | `ServerIdentity` / `MCPToolDescriptor` / `ToolCatalogSnapshot` / `MCPToolResult` / `MCPToolRuntimeState` / `RawToolOutcome` |
| `errors.py` | `MCPErrorCode`（12 个）+ `MCPError` + server 错误码→host 错误码映射 |
| `policy.py` | 冻结 allowlist + `MCPToolPolicy` + `build_child_env()`（最小子进程环境） |
| `client.py` | `MCPConnection` 协议 + `_StdioMCPConnection`（唯一触碰官方 SDK 之处） |
| `runtime.py` | `MCPToolRuntime`（Host facade）+ `build_mcp_tool_runtime()` |

## 4. 进程生命周期

```text
DISABLED → NOT_STARTED → READY ⇄ UNHEALTHY → CLOSED
```

- `MCPToolRuntime` 是 async context manager（`__aenter__/__aexit__` → `close()`）。
- **懒连接**：构造时不 spawn；第一次 `list_tools()` / `call_tool()` 才 spawn + initialize +
  list_tools + 目录校验。`initialize/list_tools` 只执行一次，长连接复用（不是每次 call 都 spawn）。
- `close()` 关闭 session 并终止子进程；官方 stdio transport 的 shutdown 是 bounded 的
  （关 stdin → 等待 → SIGTERM → SIGKILL），且在 cancellation shield 内执行，不残留 zombie、
  不泄漏子进程。
- 服务端意外退出 → 下一次调用懒重连。

## 5. Child Secret Isolation

- `build_child_env()` 从显式 allowlist（`PATH/HOME/LANG/LC_ALL`）构造，**绝不 `os.environ.copy()`**。
- 生产父进程持有的 `ZGLAB_RAG_LLM_API_KEY` / `ZGLAB_RAG_SEARCH_API_KEY` / auth / cookie /
  activation secrets 不会进入 child。
- `command/args/cwd` 是 owner deployment configuration，绝不来自 question / tool arguments /
  HTTP / LLM 输出；无 `shell=True`，使用 `executable + argv`。
- 有测试证明：真实子进程看不到父进程的 secret env（见 `tests/test_mcp_runtime.py`）。

## 6. Host Allowlist（核心安全边界）

- 不信任 server 的 `tools/list`；Host 有自己显式冻结的 allowlist（`policy.MCP_TOOL_ALLOWLIST`，
  10 个 13A/13B 工具）。
- Server 多返回 `shell_exec` / `filesystem_write` / `github_push` 等 → 忽略；`call_tool` 在发往
  server 前就拒绝（`MCP_TOOL_NOT_ALLOWED`），server call 计数为 0。
- Server 缺少某个 expected tool 或 schema 不完整 → `MCP_CONTRACT_MISMATCH`（13C 要求 10 个全在，
  不做 capability negotiation）。
- Server `annotations`（readOnlyHint/destructiveHint/openWorldHint）只是 hint，从不用于授权。

## 7. Tool Catalog

首次连接后 `initialize → list_tools`，校验：

```text
server identity（name 必须 == mcp_expected_server_name）
protocol version（记录，不硬绑 patch）
expected tool ids（10 个全在）
schema presence（每个 input_schema 必须是 object）
```

结果保存为 `ToolCatalogSnapshot`（含 `ServerIdentity`），当前 session 内复用，不每次
`call_tool` 都重新 `tools/list`。

## 8. Structured Result Mapping

`structured_content` 是权威结果（13B 契约：`{status, result}` / `{status, code, message, details?}`）：

```text
is_error=false  → {status:"success", result} → MCPToolResult(output=result)
is_error=true   → {status:"error", code, message} → 校验 code → 映射到 host 错误码
```

- `content[0].text` 只是协议兼容层，绝不作为机器权威结果。
- `structured_content` 缺失 / 状态非 success → `MCP_PROTOCOL_ERROR`（不猜）。
- server 返回未知错误码 → `MCP_INTERNAL_ERROR`（不把任意字符串变成内部异常类型）。
- 已知 server 码映射：`INVALID_INPUT/INPUT_TOO_LARGE/UNSUPPORTED_OPTION → MCP_INVALID_INPUT`、
  `OUTPUT_TOO_LARGE → MCP_OUTPUT_TOO_LARGE`、`EXECUTION_TIMEOUT → MCP_CALL_TIMEOUT`、
  `TOOL_NOT_FOUND → MCP_TOOL_NOT_FOUND`、`TOOL_DISABLED → MCP_TOOL_NOT_ALLOWED`、
  `INTERNAL_TOOL_ERROR → MCP_INTERNAL_ERROR`。

## 9. Tool Result ≠ Evidence

`MCPToolResult` 是 Tool Observation，**不是** `EvidenceItem`，也不伪造 citation / source URL /
chunk id。它与 Phase 12 的 `CapabilityResult`（GenerationResult/Evidence）保持概念隔离，Phase
14 才引入统一的 `AgentObservation`。

## 10. 输入 / 输出 Bound

- Host 发送前校验 `arguments` 序列化字节数 ≤ `mcp_max_request_bytes`（默认 512 KiB，≤ server
  1 MiB frame 上限），超限在发送前拒绝，server call 计数为 0。
- Host 收到后校验 `result` 序列化字节数 ≤ `mcp_max_response_bytes`（默认 512 KiB），超限 →
  `MCP_OUTPUT_TOO_LARGE`。

## 11. Deadline / Timeout

- 用 `asyncio.timeout` 实现 `startup_timeout` / `call_timeout` / `shutdown_timeout` 三个 deadline。
- **硬 deadline 成立**：`call_tool` 超时 → `MCP_CALL_TIMEOUT` → session 标记 `UNHEALTHY` →
  关闭/终止 child（官方 transport 的 bounded shutdown 保证进程终止）→ 下一次调用 lazy 重连新
  server。13B 的 `timeoutMs` 只是 advertised policy，13C 的 host deadline 才是真正兜底。
- 无自动重试：failure → 关闭 unhealthy session → 当前调用返回失败；下一次新调用才 lazy reconnect。
- 并发 = `mcp_max_concurrent_calls`（默认 1，Semaphore 串行）；取消时锁/会话状态一致，不永久死锁。

## 12. Kill Switch / 配置

`mcp_enabled=false`（默认）时：Personal / Web Research / Auth / API 完全不受影响；`/ready`
不依赖 Node / zglab-tools 存在。相关配置见 `src/zglab_rag/config.py`（`mcp_*` 段）与
`.env.example`。`ProductionRuntime.mcp_tool_runtime` 懒加载、disabled by default。

## 13. Cross-repo Integration（Definition of Done）

- 默认 pytest 不依赖 sibling repo：单测用注入的 fake `MCPConnection`。
- 真实跨语言验收 opt-in（`ZGLAB_RAG_MCP_INTEGRATION=1` + `ZGLAB_RAG_MCP_SERVER_CWD`），或
  `uv run python -m zglab_rag.evaluation.mcp_integration --command node --args dist-mcp/cli.js --cwd <zglab-tools>`。
- 见 `docs/evaluations/phase-13c-mcp-client-integration.md`。

## 14. Non-goals

LLM tool selection / Agent Planner / ReAct / tool loop / 自动 Personal/Web/Tool 选择 /
`/api/v2/tool|mcp|agent` / `mode=tool` / public MCP / Nginx MCP route / `ToolResult→Evidence` /
`shell=True` / 继承全部父环境 / 用户输入决定 executable / filesystem / GitHub / SSH / deploy /
browser automation。

## 15. Phase 13D Boundary

生产打包（systemd/process ownership）、quota / 用户级 audit、安全评估、deployment / rollback、
production acceptance —— 均为 13D，本阶段不实现。

## 16. Phase 13D 本地安全验收

`tests/test_mcp_process_lifecycle.py` 是 opt-in 的真实跨语言生命周期测试（需要
`ZGLAB_RAG_MCP_INTEGRATION=1` 和 sibling `zglab-tools`）。它由 Python Host 启动 test-only
Node stdio child，并通过 child PID 文件证明：

- hard call timeout 返回 `MCP_CALL_TIMEOUT`，旧 PID 已死亡，下一次连接成功；
- caller cancellation 与 child unexpected exit 都会关闭 process/session，且不会留下 child；
- 设置假的 `OPENAI_API_KEY`、`ZGLAB_RAG_SEARCH_API_KEY`、`ZGLAB_RAG_TEST_SECRET` 后，真实
  Node child 不会继承它们。

该测试不向正式 tool 制造死循环，也不新增任何公网 endpoint。生产启用仍以前置 Node 版本、
artifact 权限、internal smoke 和 kill-switch 演练全部通过为条件；真实结果见
`docs/evaluations/phase-13-production-acceptance-2026-08-28.md`。
