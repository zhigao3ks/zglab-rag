# Agent 架构（Phase 14A）

Phase 14A 建立 framework-free Agent Domain Model：`AgentRequest` 经由**显式指定**的
Personal、Web 或 MCP adapter，产出 request-scoped `O1/O2…` observations。

Personal/Web observation 保留原 `CapabilityResult`，其 Evidence/citation 语义不变；
`ToolObservation` 只含 tool id、structured result 或安全错误，绝不生成 EvidenceItem、citation
或 fake source。当前没有 Router、Planner、LLM selection、loop、final synthesis 或 API 变更。

Phase 14B 才可在此契约上实现 bounded Router/Planner（预留上限：约 4 steps、Web ≤1、MCP ≤3）。
