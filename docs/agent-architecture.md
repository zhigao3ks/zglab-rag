# Agent 架构（Phase 14 — SEALED）

Phase 14 已于 2026-08-28 完成产品接入、生产验收并封板。

生产链路：

```text
AgentRequest
    ↓
BoundedPlanner
    ↓
Validated AgentPlan
    ↓
BoundedAgentExecutor
    ↓
AgentObservation[]
    ↓
AgentSynthesizer
    ↓
AgentAnswer
```

## 1. Domain Model

Agent Domain Model 保持 framework-free。

`AgentRequest` 经过 Planner 产生 bounded plan；执行后得到 request-scoped `AgentObservation[]`。

Phase 15B additive 增加 server-derived `conversation_context`：它只作为低信任的连续语义参考
透传到 Personal/Web capability，不能影响 Planner 的 capability selection、冻结 plan、step
budget 或 MCP allowlist，也绝不是 Evidence 或 ToolResult reuse。

Observation 分为：

```text
AgentObservation
├── Personal / Web capability observation
│      └── 可保留 CapabilityResult / Evidence / provenance
└── ToolObservation
       └── tool id + structured result / safe error
```

`ToolObservation` 不生成 EvidenceItem、citation 或 fake source。

## 2. Planner

当前 `BoundedPlanner` 以 deterministic fast path 为主，不是开放式 autonomous planner。

支持的第一版能力组合包括：

- Personal；
- Web；
- Tool；
- Personal → Web 等冻结的 bounded 组合。

Planner 只提出计划：

```text
Planner proposes
Executor enforces
```

Web content、ToolResult 或模型输出均不能自行修改已验证的 plan。

## 3. Executor

`BoundedAgentExecutor` 顺序执行，并再次强制预算与 deadline。

生产冻结限制：

```text
max steps = 4
Personal <= 1
Web <= 1
MCP <= 3
overall deadline
```

执行策略：

- sequential；
- no automatic retry；
- no replanning；
- no infinite ReAct；
- dependency failure 产生 bounded / blocked observation；
- budget violation 在 Executor 再次拒绝。

## 4. Evidence / Tool Boundary

必须始终保持：

```text
ToolResult != Evidence
```

Personal / Web capability 可以产生可验证 Evidence；ToolResult 根据 tool contract 验证，但不被伪装成事实证据。

Web observation 始终为 untrusted evidence：

- 网页内容不是 system instruction；
- 不能修改 Agent plan；
- URL 只能来自真实 provenance；
- 不自动写入 Personal Knowledge。

## 5. MCP Authorization

MCP Server annotation 只作为 metadata / hint。

真正授权边界是 Agent Host / MCP Host 的 allowlist 与 server-side policy。

生产第一版只开放已批准的 deterministic / side-effect-free tool，MCP 通过 Node 22 internal stdio runtime 提供，不暴露公网 MCP endpoint。

## 6. Synthesis

`AgentSynthesizer` 的冻结行为：

- 单 Personal / Web：优先复用已有 grounded answer 与 provenance；
- 单 Tool：确定性渲染 structured result；
- 多能力 plan：才进入 final synthesis；
- synthesis 不能把 ToolResult 升格为 Evidence；
- source 只来自 Personal / Web provenance。

## 7. Product Integration

Phase 14D 已完成 authenticated `mode=agent` 接入：

```text
Origin / AuthN / AuthZ / CSRF
→ kill switch
→ question controls
→ global + agent concurrency
→ agent quota
→ bounded Agent Runtime
```

Auth / quota / concurrency / kill switch 全部位于 Agent Runtime 外部。

生产最终状态：

```text
AGENT_ENABLED=true
```

Agent SSE 只公开：

```text
accepted
→ planning
→ executing
→ synthesizing
→ validating
→ completed
```

不暴露：

- plan；
- observation；
- tool args / raw result；
- 网页正文；
- Prompt；
- 内部推理。

## 8. Production Acceptance

Phase 14 生产验收已经验证：

- Personal Agent smoke；
- Web Agent smoke；
- Tool Agent smoke；
- Personal + Web multi synthesis；
- Agent OFF fail-closed；
- anonymous / CSRF / quota / concurrency boundary；
- request-scoped MCP host runtime；
- Tool string result 正确渲染且无 source；
- 双库 migration / backup / rollback；
- `budget_violation_rate=0`；
- `unauthorized_tool_execution=0`。

完整证据见 `docs/evaluations/phase-14-production-acceptance-2026-08-28.md`。

## 9. Post-Phase 14 Boundary

Phase 14 已封板，不在当前阶段增加 retry、replanning、LLM analyst 或 ReAct。

当前立即任务是 UX Track。

后续能力顺序：

```text
Phase 15  Conversation & Session Memory
Phase 16  Retrieval Intelligence & Knowledge Graph
Phase 17  Agent Analyst
Phase 18  Advanced Agent Autonomy / Bounded ReAct
Phase 19  Owner Agent / Advanced Permissions
```

任何 Phase 15+ 工作都必须等待明确授权。
