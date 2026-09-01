# Phase 15B — Multi-turn Context Evaluation

本阶段采用 deterministic/fake integration tests 进行工程验收，未伪造在线模型质量分数。
真实模型 smoke 待后续生产验收执行。

| Case | Engineering acceptance |
| --- | --- |
| pronoun / entity carry-over | bounded previous completed context reaches capability |
| Personal / Web / Agent | shared server-derived context contract |
| no conversation | context is `None`; legacy single-turn path |
| long history | max turns / total chars / message chars hard-bounded |
| orphan turn | dangling USER excluded |
| owner / conversation isolation | owner-scoped repository and API binding |
| Agent tool follow-up | current tool intent + explicit recent USER label only resolves argument |
| history injection | regression test proves it cannot route a tool, change policy or allowlist |

Acceptance metrics: `cross_owner_context_leak=0`, `cross_conversation_context_leak=0`, `context_budget_violation=0`, `history_as_evidence=0`, `unauthorized_capability_escalation=0`, `legacy_no_conversation_regression=0`.
