# Agent Planner（Phase 14B）

`BoundedPlanner` 只把 `AgentRequest` 转成 Pydantic 验证的 `AgentPlan`，绝不执行步骤。
确定性策略优先：个人事实→Personal；当前信息→Web；明确 JSON/Base64/text/timestamp 操作→
allowlisted Tool；歧义→Personal。个人事实即使带 current 也优先 Personal；比较类请求才生成
Personal→Web 两步计划。

计划最多 4 步，Personal≤1、Web≤1、Tool≤3；tool id 必须在 host allowlist，依赖只能引用前序步骤。
没有 Router LLM、ReAct、执行、observation chaining 或 API 变更。
