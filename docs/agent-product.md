# Agent Product（Phase 14D）

`POST /api/v2/ask` 与 `/api/v2/ask/stream` 支持显式 `mode=agent`。`auto`、`personal`、`web`
行为保持不变；`AGENT_ENABLED=false` 默认 fail-closed。

安全顺序是 Origin、AuthN/AuthZ、CSRF、kill switch、question controls、global/agent concurrency、
agent quota、bounded runtime。独立的 `agent_usage` table 使用 auth schema v4。

SSE 仅发送 `accepted → planning → executing → synthesizing → validating → completed`。不暴露 plan、
observation、工具参数/结果、网页正文或推理。sources 仅来自 Personal/Web provenance；Tool result 永不成为 source。
