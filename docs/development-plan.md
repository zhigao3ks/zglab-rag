# 开发计划

本路线图用于在 Codex 协助下渐进开发。每个 Phase 都应能够独立测试，不提前实现后续复杂能力。

> **Roadmap Rebaseline — 2026-08-25**
>
> Phase 0–10 的历史编号、实现内容与验收结果保持不变。
> Phase 11 及以后已经重新基线，权威定义见 `docs/roadmap-v2.md`。
> 仓库在 2026-08-21 曾把 Web Research 冻结为 Phase 11；该技术设计仍有效，但现顺延为 Phase 12，
> Session Context 统一移动到 Phase 15。

## Phase 0–10 — Personal Knowledge Assistant Foundation

状态：**已完成并完成生产部署。**

### Phase 0 — Architecture

完成来源注册表、public/private 边界、Domain contract、可替换 AI 组件与仓库规则。

### Phase 1 — Markdown Ingestion

完成 frontmatter、Markdown 结构感知 Chunking、稳定 ID、SHA-256 内容哈希与测试。

### Phase 2 — Knowledge Source Acquisition

完成 local / local Git source adapter、include/exclude allowlist、revision provenance；同步与读取边界分离。

### Phase 3 — Embedding Evaluation

在真实知识集上完成候选 Embedding benchmark，最终生产 baseline 使用 BGE-small-zh-v1.5 + contextual composition。

### Phase 4 — Persistent Index Lifecycle

完成 SQLite 权威 metadata、sqlite-vec、Embedding profile、增量 planner、事务安全 build/rebuild 与失败审计。

### Phase 5 — Production Vector Retrieval

完成只读 `VectorRetriever`、public/source/scope 过滤、over-fetch、profile validation 与真实持久化 index evaluation。

### Phase 6 — Lexical / Hybrid Evaluation

完成 FTS5 trigram lexical retrieval 与 RRF Hybrid；评测后 Hybrid 未超过 Vector，因此生产默认仍为 Vector。

### Phase 7 — Reranker Evaluation

完成 CrossEncoder Reranker 实现与 CPU benchmark。质量明显提升，但当前 2C2G 生产预算下默认仍使用 Vector，
`reranked` 作为显式可选模式保留。

### Phase 8 — Grounded Generation

完成 Question → Retrieval → Evidence Context → LLM → Citation Validation 的固定 Workflow。
Persona 不是证据来源，claim-level citation 与 insufficient-evidence 由确定性代码约束。

### Phase 9 — Public Assistant Product Layer

状态：**已完成（9A / 9B / 9C / 9D）**。

完成：

- Public API v1；
- safe error envelope；
- concurrency / rate limit / timeout / request-size boundary；
- status SSE；
- Vue Web Assistant；
- Public API / SSE / Grounding / Web UX / Security product acceptance。

Public API v1 的历史冻结定义见 `docs/public-api.md`。

### Phase 10 — Production Sync & Deployment

状态：**已完成并公网验收。**

生产地址：`https://ask.zglab.fun`

完成：

- Nginx + HTTPS；
- FastAPI / Uvicorn systemd service；
- Vue SPA + history fallback；
- source sync service / timer；
- SQLite atomic backup + retention；
- `/health` / `/ready`；
- 普通 API / SSE 公网验证；
- production acceptance。

详细记录见：

- `docs/production-architecture.md`
- `docs/evaluations/phase-10-production-acceptance.md`

---

# Roadmap v2 — Personal AI Agent

长期产品目标已经从单纯的 Personal Knowledge Assistant 扩展为：

> **以个人身份为主体的 ZGLab Personal AI Agent。**

最终 Agent 逐步组合：

```text
Personal Knowledge Skill
+ Web Research Skill
+ MCP Tool Runtime
+ Agent Orchestration
```

但在任何新的 cost-bearing capability 上线前，先建立身份认证、授权、Session 吊销与用户级额度边界。

当前权威 Phase：

```text
Phase 11    Authentication & Access Control             ✅ 已完成并生产验收
Phase 12A   Capability Foundation & PersonalKnowledgeSkill ✅ 已实现
Phase 12B   Web Research Core                           ✅ 已实现
Phase 12C   Evidence + Grounded Generation Integration  ✅ 已实现
Phase 12D   Product Integration & Evaluation            ✅ 已完成并生产验收/封板
Phase 13A   Tool Core Boundary & MCP Contracts          ✅ 已完成
Phase 13B   MCP Server Runtime                          ✅ 已完成
Phase 13C   MCP Client + Capability Integration         ✅ 已完成
Phase 13D   Security / Evaluation / Production          ✅ COMPLETE / PRODUCTION ACCEPTED / SEALED
Phase 14A   Agent Contracts & Observation Model         ✅
Phase 14B   Router / Bounded Planner                    ✅
Phase 14C   Executor & Final Synthesis                  ✅
Phase 14D   Product / Evaluation / Production           ✅ COMPLETE / PRODUCTION ACCEPTED / SEALED
Phase 15    Session Context                             ← NEXT
Phase 16    Owner Agent / Advanced Permissions
```

## Phase 11 — Authentication & Access Control

状态：**已实现（11A / 11B / 11C / 11D），2026-08-26 已完成生产迁移与验收并封板。**

设计与验收：

- `docs/authentication.md`
- `docs/api-v2.md`
- `docs/evaluations/phase-11-authentication-acceptance.md`

核心目标：让公开展示继续可访问，但所有会触发 LLM / Search / MCP / Agent 成本的能力必须经过身份认证、
授权与用户级成本边界。

### 产品访问模型

```text
ask.zglab.fun
      │
      ├── Public Landing / Project Showcase
      │       └── 不触发外部消费
      │
      └── Authenticated Application
              └── RAG + future Agent capabilities
```

### 冻结原则

1. **禁止公开注册**：不存在匿名 `/register` / `/signup`；
2. 用户只能由管理员 CLI 创建和下发；
3. 采用 Admin Provisioning + Single-use Activation Token；
4. 密码使用成熟 Argon2id 实现；
5. 默认使用 Server-side Session + Secure HttpOnly Cookie，而不是 JWT + localStorage；
6. Auth 数据使用独立 `auth.db`，不写入 `knowledge.db`；
7. Activation / Session Token 数据库只存 hash；
8. Authentication / Authorization 必须在服务端强制，前端 route guard 只负责 UX；
9. Cookie Session 必须设计 CSRF / Origin 防护；
10. Login 至少有 per-IP + per-identity throttling；
11. Consumer API 至少有 per-user rate limit / daily quota；
12. 继续保留 Phase 9 的 request-size、timeout、concurrency、safe error 等防护；
13. `/health`、`/ready` 与 Public Landing 保持匿名；
14. Login 不等于 private knowledge 开放，现有 knowledge retrieval 仍强制 public；
15. Phase 11 不实现 Web Research、MCP、Agent Planner、Session Memory。

### API 演进方向

Public API v1 是 Phase 9 的历史冻结契约。Phase 11 应优先设计新的 authenticated `/api/v2`，例如：

```text
POST /api/v2/auth/login
POST /api/v2/auth/logout
GET  /api/v2/auth/me
POST /api/v2/auth/activate
POST /api/v2/auth/change-password
POST /api/v2/ask
POST /api/v2/ask/stream
```

旧 `/api/v1/ask` / `/api/v1/ask/stream` 在生产迁移完成后不得继续作为匿名消费入口。
具体 compatibility / retirement policy 由 Phase 11 设计文档冻结。

### Phase 11A — Identity Core

交付：

- `auth.db` + explicit schema version；
- User model / repository；
- Argon2id password hashing；
- Admin CLI；
- high-entropy single-use activation token；
- password reset token abstraction；
- secret-safe logs；
- unit tests。

### Phase 11B — Session Authentication

交付：

- login / logout / me / activate / change-password；
- server-side Session；
- Secure / HttpOnly / SameSite Cookie；
- Session expiration / revoke；
- CSRF / Origin validation；
- login throttling；
- 前端 login / activate / auth-state 恢复。

### Phase 11C — Protected API & Cost Boundary

交付：

- authenticated `/api/v2/ask`；
- authenticated `/api/v2/ask/stream`；
- SSE 与普通 Ask 共用 Security Boundary；
- per-user rate limit；
- daily request quota；
- server-side authorization；
- 保持 public-only knowledge retrieval。

### Phase 11D — Security / Product Acceptance

交付：

- audit events；
- LLM / future capability kill-switch contract；
- Public Landing；
- production migration / bootstrap admin 流程；
- Auth acceptance matrix；
- 全量 regression。

### Phase 11 Non-goals

- Web Research；
- MCP；
- Agent Planner / Executor；
- private knowledge mode；
- Redis；
- OAuth/OIDC server；
- Email service；
- Web Admin Console；
- Full Conversation Memory。

## Phase 12 — Agent Capability Foundation & Web Research

状态：**12A / 12B / 12C / 12D 已完成；Phase 12 已于 2026-08-28 完成生产验收并封板。生产 Web Research 已开启；真实 provider smoke、HTTPS API/SSE、安全开关、quota/concurrency、provider 故障隔离、auth v3 与备份均已验证。**

原 2026-08-21 的 Web Research 设计顺延到本阶段。详见 `docs/web-research-skill.md`。

目标：

- 现有 RAG 抽象为 `PersonalKnowledgeSkill`（✅ 12A 已完成）；
- 建立最小 Capability / Skill contract（✅ 12A 已完成）；
- 实现 request-scoped `WebResearchSkill`（✅ 12B 已完成）；
- SearchProvider abstraction（✅ 12B 已完成，Tavily + fake）；
- Search → candidate selection → safe fetch → extraction → normalization（✅ 12B 已完成）；
- External Evidence → Grounded Generation → Citation Validation（✅ 12C 已完成，仅内部能力）；
- SSRF / Prompt Injection / URL provenance 安全边界（✅ 12B 已建立；
  ✅ 12C 已把 Prompt Injection 的 LLM data boundary 接入 generation；
  ✅ 12D 以 pinned resolution 关闭 DNS rebinding TOCTOU）；
- Product Integration：确定性 capability selection / mode / web source /
  researching SSE / 独立 quota（✅ 12D 已完成并生产验收）；
- Research Evaluation（✅ 12D 已建立 dataset 与 offline harness；真实
  provider smoke 已在生产验收中通过）。

12A 设计与验收：`docs/capability-architecture.md`、
`docs/evaluations/phase-12a-capability-foundation.md`；12B 设计与验收：
`docs/web-research-runtime.md`、
`docs/evaluations/phase-12b-web-research-core.md`；12C 设计与验收：
`docs/web-evidence-grounding.md`、
`docs/evaluations/phase-12c-web-evidence-grounding.md`；12D 产品接入与
验收：`docs/web-research-product.md`、
`docs/evaluations/phase-12d-product-acceptance.md`、
`docs/evaluations/phase-12-web-research-evaluation.md`；生产封板记录：
`docs/evaluations/phase-12-production-acceptance-2026-08-28.md`。

Phase 12 不实现完整 Session Evidence Reuse；旧文档中该部分移到 Phase 15。

## Phase 13 — MCP Tool Runtime

状态：待实现。

目标：把 `zglab-tools` 中适合机器调用的纯逻辑工具通过 MCP 暴露给 Agent。

原则：

- 不机械 MCP 化全部工具；
- 优先无副作用、确定性、低风险工具；
- Web UI 与 MCP Server 尽量复用 Shared Tool Core；
- 生产初版 MCP Server 优先 localhost/internal，不直接暴露公网；
- 权限由 Agent Host / Policy Engine 强制，而不是只依赖 Tool annotation。

## Phase 14 — Agent Orchestrator

状态：待实现。

在 PersonalKnowledgeSkill、WebResearchSkill、MCP Runtime 都稳定后，引入：

```text
Capability Registry
Router / Planner
Policy Engine
Bounded Executor
Observation Model
Final Synthesis / Validation
```

第一版使用 Bounded Planner + Executor，不使用无限 ReAct loop。

必须限制 max steps、research count、tool calls、deadline 与 capability permissions。

## Phase 15 — Session Context

状态：待实现。

统一处理：

- limited conversation reference context；
- Temporary Web Evidence Reuse；
- Tool Artifact Reuse；
- lightweight ephemeral session store；
- TTL / max sessions / max items / max bytes。

Personal Knowledge、Web Evidence、Session Context、Long-term Memory 必须保持概念隔离。

## Phase 16 — Owner Agent / Advanced Permissions

状态：待实现。

在普通 authenticated user 的安全低副作用能力成熟后，再设计 Owner-only capability：

- private sources；
- GitHub write；
- file / deploy / admin operations；
- step-up authentication；
- human confirmation；
- destructive operation policy。

## Evaluation 的定位

Evaluation 继续是贯穿项目的基础设施：

- Phase 3 Embedding Evaluation
- Phase 5 Vector Retrieval Evaluation
- Phase 6 Hybrid Evaluation
- Phase 7 Reranker Evaluation
- Phase 8 Generation Evaluation
- Phase 9 / 10 Acceptance
- Phase 11 Auth / Security Acceptance
- Phase 12 Research Evaluation
- Phase 13 MCP contract / safety tests
- Phase 14 Agent routing / execution evaluation

不再单独建设一个“Evaluation Phase”。

## Post-v1 Optimization

以下方向继续作为非编号优化轨道：

- Reranker ONNX / INT8；
- Answer latency；
- token / context budget；
- caching；
- monitoring / metrics；
- evaluation expansion；
- answerability / rejection 研究；
- advanced Hybrid tuning。

它们不得抢占 Product Capability Phase 编号，也不得被 Codex 误认为当前 Phase 的必需实现。

## Codex 任务规则

要求 Codex 实现 Phase 时，每次任务只包含一个 Phase 或一个明确垂直切片。

任何 Phase 11+ 任务开始前必须先阅读：

1. `AGENTS.md`
2. `docs/roadmap-v2.md`
3. 本文档
4. 当前 Phase 对应设计文档

如果历史 acceptance、旧 issue、旧 comment 或旧文档中的 future-phase 编号与 `docs/roadmap-v2.md` 冲突：

> **以 `docs/roadmap-v2.md` 为准，不自行混合两个 Phase。**

当前下一 Product Phase 唯一允许开始的是：

> **Phase 11 — Authentication & Access Control**
