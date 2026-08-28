# Phase 14 生产验收

日期：2026-08-28 ｜ 结论：**COMPLETE / PRODUCTION ACCEPTED / SEALED**。

生产部署提交依次为 `8302ae9`（产品集成）、`0f18c15`（Agent MCP worker event-loop ownership）与
`7f9e4b7`（Tool 字符串结果直接渲染）。部署前创建 rollback snapshot
`/opt/zglab-rag/rollback/phase14-20260828T163700Z/`，包含 app、`.env`、knowledge.db 与 auth.db。
双库部署前后均 `integrity_check=ok`；auth schema 从 v3 原子迁移至 v4，`agent_usage` 与既有
`web_usage` 均存在，最后双库 backup service 返回 success。

首次部署保持 `AGENT_ENABLED=false`。真实 ADMIN HTTPS/browser regression 通过：login、Personal、
Personal SSE、Web、Web SSE、MCP internal smoke 与 logout/session；Agent OFF 返回
`503 CAPABILITY_DISABLED`，旧能力不受影响。

开启 Agent 后，health/ready 均为 200。真实 Agent Personal 与 Web smoke 通过；首次 Tool smoke 暴露
MCP stdio client 在一次性 worker event loop 外拥有长生命周期连接，导致 90 秒 timeout 并占用全局 slot。
立即关闭 Agent 并重启 API。修复后 Tool 使用 request-scoped MCP host runtime，Tool 与 Personal+Web
multi synthesis 均通过；随后修复 Tool JSON 字符串的二次 JSON 编码，最终显示格式化结果且没有 source。

安全验收：匿名 Agent 为 `401 AUTHENTICATION_REQUIRED`；Agent OFF 为 `CAPABILITY_DISABLED`；server-side
CSRF、独立 agent quota/concurrency、max steps=4、Personal≤1、Web≤1、MCP≤3 由完整测试与部署配置共同约束。
没有 ToolResult→Evidence/source/citation 路径。SSE 仅公开 accepted/planning/executing/synthesizing/
validating/completed，不暴露 plan、observation、tool raw data、网页正文或推理。

最终运维状态：`zglab-rag-api.service`、Nginx、backup timer、sync timer 均 active；health/ready=200；
`AGENT_ENABLED=true`。核心评测 gate：`budget_violation_rate=0`、`unauthorized_tool_execution=0`。
