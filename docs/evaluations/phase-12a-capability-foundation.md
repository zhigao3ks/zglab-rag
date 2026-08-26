# Phase 12A — Capability Foundation 验收

日期：2026-08-26
范围：仅 Phase 12A（Capability Foundation & PersonalKnowledgeSkill）。
Phase 12B/12C/12D（Web Research 及后续）未开始。

## 1. 交付物

| 交付项 | 位置 | 状态 |
| --- | --- | --- |
| Capability contract | `src/zglab_rag/capabilities/contracts.py` | ✅ |
| Capability error model | `src/zglab_rag/capabilities/errors.py` | ✅ |
| Capability registry | `src/zglab_rag/capabilities/registry.py` | ✅ |
| PersonalKnowledgeSkill | `src/zglab_rag/capabilities/personal_knowledge.py` | ✅ |
| Runtime 集成 | `api/runtime.py`（ProductionRuntime）、`application/runtime.py`（ApplicationRuntime） | ✅ |
| `/api/v2/ask` 集成 | `api/main.py::_execute_generation` 经 skill boundary | ✅ |
| SSE 兼容 | 阶段事件与 envelope 零改动（回归测试覆盖） | ✅ |
| 架构文档 | `docs/capability-architecture.md` | ✅ |

核心代码量：capabilities 包约 370 行（含注释/docstring），符合"几百行以内"
的最小抽象目标；无新依赖、无新配置项。

## 2. Acceptance Gates 对照

| # | Gate | 证据 |
| --- | --- | --- |
| 1 | 存在明确 Capability contract | `contracts.py::Capability` Protocol |
| 2 | 存在 PersonalKnowledgeSkill | `personal_knowledge.py` |
| 3 | Skill 复用现有 RAG，不重写 | skill 仅调用 `runtime.create_service().answer()`；retrieval/generation/citation 代码零改动 |
| 4 | API v2 经 Skill boundary 调用 | `_execute_generation` → `capability_registry.get("personal_knowledge").execute()`；测试以 runtime 连接/服务创建计数证明 |
| 5 | API response contract 不变 | `_map_result_to_response` 未改动；`test_v2_ask_runs_through_capability_boundary` 断言 request_id/status/answer/sources |
| 6 | SSE contract 不变 | `_stream_events` 未改动；`test_v2_stream_event_contract_unchanged`；Phase 9B 回归测试全通过 |
| 7 | Citation Validation 不变 | `generation/citation.py` 零改动；GenerationResult 原样透传 |
| 8 | public-only retrieval 不变 | skill 不触碰 visibility；`CapabilityRequest` 只有 question 字段（字段集测试锁定） |
| 9–13 | AuthN/AuthZ/CSRF/quota/concurrency 不变 | Phase 11 全部测试通过；新增测试证明 401/403/429 均在 capability 执行前拒绝（连接计数为 0） |
| 14 | LLM kill switch 不变 | Phase 11 测试通过；kill switch 仍在安全门内先于 capability 评估 |
| 15 | insufficient ≠ failure 语义明确 | `test_insufficient_evidence_is_business_result_not_failure`、`test_technical_failure_raises_typed_error_with_original` |
| 16 | Registry 不承担 Router | registry 只有 register/get/list_metadata；无 choose/route 方法 |
| 17 | 无 Web Search 实现 | 无 SearchProvider / fetcher / SSRF 相关新代码 |
| 18 | 无 MCP | 无 MCP 相关代码 |
| 19 | 无 Planner | 无 planner/router/agent 代码 |
| 20 | Phase 0–11 regression 全通过 | 全量 pytest（结果见第 3 节） |

## 3. 测试结果（真实执行）

```text
uv run pytest -q          → 352 passed（330 回归 + 22 capability 新增）
uv run ruff check .       → All checks passed
git diff --check          → 后端无真实空白问题（api/runtime.py、
                            application/runtime.py 的 CRLF 基线告警为仓库
                            既有基线，与 Phase 11 web 目录同类处理）
前端                      → 本次无前端改动（API contract 未变），无需重跑
```

新增测试（`tests/test_capabilities.py`，22 例）：

- Contract：id 稳定、request 仅含 question（拒绝额外字段）、context 不携带
  HTTP 状态、状态映射、metadata 标志；
- Registry：注册/查询/列举、重复 id 拒绝、未知 id 拒绝、12A 只注册
  personal_knowledge；
- Skill：answered→SUCCESS 且 sources/claims 原样保留、insufficient 是业务
  结果、FAILED 映射带 failure_reason、技术故障抛 CapabilityTechnicalError
  且 original 可解包、progress 回调逐阶段转发、request-scoped 连接生命
  周期；
- API 集成：v2 ask 经 capability 执行且响应契约不变、SSE envelope 不变、
  匿名/CSRF/quota 拒绝发生在 capability 之前、ADMIN 无额外知识路径。

## 4. 明确未做（Phase 12B+）

WebResearchSkill、SearchProvider、Web Fetcher、External Evidence、
researching SSE、任何 capability selection policy / planner、MCP、
Session Context、Owner capability。

## 5. Remaining Risks（真实遗留）

1. `CapabilityContext` 目前仅被 PersonalKnowledgeSkill 透传，尚未参与任何
   policy 决策——这是有意的最小设计，12B 引入第二个 Skill 时才会产生真实
   消费方；
2. `_execute_generation` 对 `CapabilityTechnicalError` 的解包依赖
   `original` 字段；若未来 Skill 抛出不带 original 的技术错误，将退化为
   INTERNAL_ERROR（已有单测锁定带 original 的路径）；
3. `EvidenceOrigin` 当前只是 advisory 标记，无任何消费方；12B 之前不应
   据此做任何 policy。
