# Agent Executor（Phase 14C）

内部链路固定为：

```text
AgentRequest → BoundedPlanner → validated AgentPlan → BoundedAgentExecutor
→ AgentObservation[] → AgentSynthesizer → AgentAnswer
```

`BoundedAgentExecutor` 只经由 Phase 14A adapter 调用 Personal、Web 和 allowlisted MCP Tool，按 step
顺序执行一次。它在入口重新验证计划及 request id，并强制最多 4 steps、Personal ≤1、Web ≤1、Tool ≤3
与总 deadline；无 retry、replan、ReAct、并行或自行 retrieval/search/MCP spawn。

dependency 仅在前置 observation 为 `success` 时执行，否则记录 `blocked` observation。deadline 到达时停止
后续步骤并返回 `AGENT_DEADLINE_EXCEEDED`。内部 trace 不保存问题、证据正文、tool input/output 或推理内容。

单 Personal/Web step 直接复用原有 validated grounded answer 和 provenance；单 Tool step 只确定性渲染
structured result。多能力计划才可调用注入的 final synthesizer。ToolObservation 不生成 Evidence、citation
或 source；Web 仍是 untrusted evidence，不能修改 plan、步骤或 tool id。

本阶段没有 API/SSE/frontend agent mode，也没有生产部署；产品接入留给 Phase 14D。
