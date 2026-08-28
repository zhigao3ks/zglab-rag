# Phase 14A Agent Contracts 验收

- PersonalCapabilityResult → `PersonalKnowledgeObservation`；
- WebCapabilityResult → `WebResearchObservation`；
- MCP success/error → `ToolObservation`；
- observation IDs 在 executor 内按 `O1/O2…` 确定性递增；
- ToolObservation 不带 Evidence/citation/source 字段。

默认测试只使用 injected fake capability，不依赖 LLM、Search 或 sibling MCP repo。Phase 14A
未修改 HTTP API、selector 或前端，未实现 Router/Planner。
