# Agent 架构（Phase 14A–14C）

Phase 14A 建立 framework-free Agent Domain Model：`AgentRequest` 经由**显式指定**的
Personal、Web 或 MCP adapter，产出 request-scoped `O1/O2…` observations。

Personal/Web observation 保留原 `CapabilityResult`，其 Evidence/citation 语义不变；
`ToolObservation` 只含 tool id、structured result 或安全错误，绝不生成 EvidenceItem、citation
或 fake source。当前没有 Router、Planner、LLM selection、loop、final synthesis 或 API 变更。

Phase 14B 才可在此契约上实现 bounded Router/Planner（预留上限：约 4 steps、Web ≤1、MCP ≤3）。

Phase 14B 已实现 deterministic `BoundedPlanner`。Phase 14C 使其经由 `BoundedAgentExecutor` 顺序执行，
并产生带 step id 的 observation 与安全 trace；执行器会重新强制步骤/能力预算和总 deadline。失败不 retry，
依赖失败仅产出 blocked observation。

`AgentSynthesizer` 直接复用单 Personal/Web 的 grounded answer 与 provenance，确定性渲染单 Tool result；
只有多能力计划可进入注入式 final synthesis。Tool result 永远不是 Evidence，Web observation 仍是 untrusted
evidence，不能修改冻结计划。上述链路仍只供内部 harness 使用，API/SSE/frontend/production integration 留给 14D。
