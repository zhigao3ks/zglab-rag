# Capability Architecture（Phase 12A）

本文档定义 ZGLab Personal AI Agent 的 **Capability Foundation**：系统有哪些
受控能力、以及如何统一调用它们。这是 Phase 12 的第一部分（12A），只落地
`PersonalKnowledgeSkill`，不实现 Web Research / MCP / Agent 编排。

## 1. 为什么需要 Capability Layer

Phase 0–11 之后，API 直接依赖 `GroundedAnswerService` 及其内部结构
（VectorRetriever / BGE / FTS5 / Citation Validation）。未来 Agent Runtime
如果继续直接依赖这些实现细节，每新增一个能力（Web Research、MCP Tool）
都要重复处理安全边界、结果语义与错误模型。

Phase 12A 在两者之间插入一个**最小稳定抽象**：

```text
                    Application / Future Agent
                              │
                              ↓
                     Capability Boundary
                              │
             ┌────────────────┴────────────────┐
             │                                 │
             ↓                                 ↓
 PersonalKnowledgeSkill                Future WebResearchSkill
             │
             ↓
 Existing GroundedAnswerService
             │
             ↓
 Retrieval → Evidence → Generation → Citation
```

原则：

> Wrap existing RAG, do not rewrite existing RAG.

Phase 0–10 验证过的 Retrieval / Generation / Citation pipeline 保持逐字节
稳定；Skill 只是它的 adapter。

## 2. Capability vs Skill vs Tool

| 概念 | 含义 | 本项目中的位置 |
| --- | --- | --- |
| Capability | 受控能力的统一调用契约（id + execute + result） | `capabilities/contracts.py`（Phase 12A 已落地） |
| Skill | Capability 的一个真实实现（封装一条完整业务管线） | `PersonalKnowledgeSkill`（已落地）；未来 `WebResearchSkill` |
| Tool | 外部可调用的原子操作（如 MCP tool） | **Phase 13**，当前不存在 |

**Skill ≠ MCP Tool**：Skill 是仓库内部、服务端受控的完整能力边界，调用方
只能通过 `CapabilityRequest` 传入问题；MCP Tool 是外部协议化的工具运行时，
属于 Phase 13，两者不共享实现。

## 3. Contract

```python
class Capability(Protocol):
    metadata: CapabilityMetadata

    def execute(self, request: CapabilityRequest, context: CapabilityContext,
                *, progress=None) -> CapabilityResult: ...
```

- `CapabilityRequest` **只有 `question` 一个字段**。客户端无法控制
  retrieval_mode / top_k / visibility / provider / model / debug / private；
  这些全部由服务端配置与 policy 决定；
- `CapabilityContext` 只携带 `request_id` 与 Phase 11 的
  `AuthenticatedPrincipal`（复用，不新建身份模型）。**不含** FastAPI
  Request、HTTP headers、cookie、Vue state、SQLite connection；
- `CapabilityMetadata`：`id / name / description / requires_auth /
  network_access`，仅此五个字段，不做 permission DSL。

## 4. CapabilityResult 与 Failure Model

```text
CapabilityStatus: SUCCESS / INSUFFICIENT_EVIDENCE / FAILED
EvidenceOrigin:   PERSONAL / WEB（WEB 仅预留，12A 只产出 PERSONAL）
```

三类结果严格区分（Phase 8 语义保留）：

| 类别 | 表达形式 | 未来 policy 含义 |
| --- | --- | --- |
| 业务证据不足 | `INSUFFICIENT_EVIDENCE` 结果（正常业务结果，不是异常） | 未来可允许尝试其他 Capability |
| 技术故障 | 抛 `CapabilityTechnicalError`（携带原始异常） | **绝不能**触发 fallback |
| 策略拒绝 | 抛 `CapabilityPolicyError`（12A 未启用） | 能力级 policy 预留 |

`CapabilityResult` 原样携带 `GenerationResult`，因此公开响应信封
（`request_id / status / answer / sources`）与 citation 语义零改动。

## 5. PersonalKnowledgeSkill

职责仅三步：

```text
接受 CapabilityRequest
  → runtime.request_connection() + create_service()（现有工厂，逐字复用）
  → GroundedAnswerService.answer(question, progress)
  → 映射为 CapabilityResult（answered→SUCCESS，insufficient→INSUFFICIENT_EVIDENCE）
```

不变量：

- **public-only**：Skill 从不触碰 visibility；登录（甚至 ADMIN）只决定谁
  能消费能力，不解锁 private knowledge（Phase 16 领域）；
- **citation 契约不变**：`answer / sources / validated claims` 原样透传；
- **progress 语义不变**：回调原样转发，SSE 阶段
  `accepted/retrieving/generating/validating/completed` 不新增不改动；
- **技术异常包装可解包**：API 层将 `CapabilityTechnicalError` 解包回原始
  异常，Phase 9 错误映射（PROVIDER_UNAVAILABLE / INTERNAL_ERROR /
  GENERATION_TIMEOUT）逐位不变。

## 6. CapabilityRegistry

```python
registry.register(capability)      # 重复 id → DuplicateCapabilityError
registry.get("personal_knowledge") # 未知 id → CapabilityNotFoundError
registry.list_metadata()           # 只读快照
```

**Registry ≠ Agent Planner**：它不根据 prompt 选择 Skill、不调用 LLM 决策、
不做自动 fallback、不循环执行。当前 runtime 只注册 `personal_knowledge`
一个真实能力；`web_research` / `mcp_tool` 等未来 id 只出现在文档中，
runtime 不注册不存在的能力。

## 7. Dependency Direction

```text
API → Capability → Application / Generation / Retrieval
```

`capabilities/` 只 import `auth.models`（身份 Domain Model）、
`generation.contracts`（结果契约）；不 import FastAPI / Starlette / Vue，
不解析 cookie，不开 SQLite 连接。SQLite connection 仍是 request-scoped、
embedding / LLM provider 仍是 app-scoped（Phase 9/10 资源生命周期不变）。

## 8. Security Boundary

Capability 位于 Phase 11 安全门**之后**：

```text
HTTP → Validation → Origin → AuthN → AuthZ → CSRF → Kill Switch
     → Question length → Concurrency → Quota → Capability
```

匿名 / CSRF 失败 / 超配额请求在 Capability 执行前就被拒绝（测试以
runtime 连接计数为 0 证明）。Quota、并发、kill switch 全部留在安全门，
Capability 内部不自维护任何认证/配额状态。

## 9. Evidence Boundary

12A 只处理 Personal Evidence。`EvidenceOrigin.PERSONAL` 作为轻量来源标记
引入；Phase 8 的 `EvidenceItem` 模型**未重构**（兼容式扩展原则）。未来
WebResearchSkill 产出 `EvidenceOrigin.WEB`，并继续复用同一 Grounded
Generation + Citation Validation 基础。

## 10. WebResearchSkill（12B/12C 已实现，未注册进 Registry）

- 现状：`research/` 提供完整 Research → Grounded Generation 内部链路
  （见 `docs/web-research-runtime.md` 与 `docs/web-evidence-grounding.md`）；
  `WebResearchSkill.answer()` 复用同一 Capability contract /
  CapabilityContext / CapabilityResult 语义（origin=WEB）；
- 边界（详见 `docs/web-research-skill.md`）：SearchProvider 可替换、
  SSRF 防护、Prompt Injection 边界、Web Evidence 不写入长期 Personal
  Knowledge；citation URL 只能来自 provenance；
- 尚未注册进 `CapabilityRegistry`，也没有任何 HTTP endpoint：产品接入、
  Personal/Web 路由与公网 contract 属于 Phase 12D；
- 12A 本身不实现任何 SearchProvider / Web Fetcher / researching SSE。

## 11. Future MCP Relationship（Phase 13，未开始）

MCP Tool Runtime 将是独立边界；未来 MCP tool 可能被 Agent Orchestrator
（Phase 14）与 Capability 并列调度，但 Skill 永远不会是 MCP tool 的
封装，反之亦然。

## 12. Non-goals（Phase 12A 明确不做）

Planner / Executor / ReAct Loop / LLM Router / Agent Graph / Tool Calling /
MCP / Multi-Agent / Autonomous Loop / Web Research 实现 / Session Memory /
Owner Tools / 新增配置项（无真实需求，未新增 kill switch）。
