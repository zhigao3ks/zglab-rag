# Phase 13 生产验收记录（2026-08-28）

> 结论：**Phase 13 production accepted: NO**。生产 `MCP_ENABLED` 保持关闭；不得封板。

## 已完成的本地安全验证

- `zglab-tools` source commit：`3586a8c373be9bec1e11d1b78ae341861782a9c2`；
  `npm run build:mcp` 生成 self-contained `dist-mcp/cli.js`（Node 22 target）与无 secret/
  本地路径的 manifest，manifest 的 `tool_count=10`、source commit 一致。
- `zglab-rag` source commit：`1bad73201160218011bcfe37a4b154ea365a083a`。
- Python official MCP Client → compiled Node server 完成 initialize、tools/list（10 个固定
  allowlist）、JSON/Base64/URL/text/timestamp calls 与 clean shutdown。
- test-only real Node hanging child 证明 hard timeout 返回 `MCP_CALL_TIMEOUT`、旧 PID 被回收、
  session reset、下一次连接成功；caller cancellation 与 unexpected child exit 同样无 child
  残留。
- 使用假的 `OPENAI_API_KEY`、`ZGLAB_RAG_SEARCH_API_KEY`、`ZGLAB_RAG_TEST_SECRET` 验证 child
  不继承 parent secret。child argv/cwd 只来自 owner config、无 shell。
- Host allowlist 与 Node `tools/list` 的 10 个 tool 一致；extra `shell_exec` 被拒绝，缺少
  expected tool 为 `MCP_CONTRACT_MISMATCH`。Host request/response limit 都是 256 KiB，与 Tool
  Core payload contract 对齐且小于 1 MiB stdio frame；oversize、malformed
  structuredContent、unknown error code 等已由 MCP unit/integration tests 覆盖。

本地测试：`zglab-tools` 156 tests + 26 MCP tests；`zglab-rag` MCP 相关 opt-in tests 22 passed。
独立 harness 实测 protocol `2025-11-25`、server `zglab-tools-mcp@0.0.1`、tool count 10、
startup 122.020 ms、9 个典型调用总计 36.967 ms、clean shutdown=true。

## 生产预检与 STOP

实际生产服务器预检（2026-08-28）：

- `/usr/bin/node` 为 **v18.19.1**；`zglab-tools/package.json` 要求 **>=22.12.0**；
- `zglab-rag-api.service`、Nginx、backup timer、sync timer 均为 active；
- localhost `/health` 与 `/ready` 均为 HTTP 200；
- 生产 `.env` 没有显式 MCP 开关，应用配置默认 `MCP_ENABLED=false`。

Node 版本不兼容触发 Phase 13D STOP condition。因此本次**没有**安装 MCP artifact、没有修改
生产 `.env`、没有 restart API、没有 internal MCP smoke、没有 MCP kill-switch/rollback 演练，也
没有新增 Nginx route、public `/mcp`、`/api/v2/tool` 或 `mode=tool`。

## 后续前置条件

在受控维护窗口升级/确认 Node >=22.12.0 后，才可重新开始：备份 app/双库/.env、安装 artifact 到
`/opt/zglab-rag/mcp/zglab-tools/<commit>/`（service user read+execute/no write）、先保持 MCP
关闭的 Personal/Web 回归，再以固定 `/usr/bin/node <artifact>/cli.js` 执行 service-user internal
smoke、kill-switch 与 artifact rollback。全部真实通过前，Phase 13 不得标记 COMPLETE、
PRODUCTION ACCEPTED 或 SEALED。
