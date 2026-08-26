# ZGLab Personal AI Agent — Roadmap v2

> **Roadmap authority**：本文档自 **2026-08-25** 起是 Phase 11 及以后阶段的权威路线图。
> Phase 0–10 的历史编号、实现内容与验收记录保持不变。
> 如果旧文档、历史验收记录或注释中的 Phase 11+ 编号与本文档冲突，以本文档为准。

## 1. 为什么重新基线

Phase 0–10 已经完成了 Personal Knowledge Assistant 的核心基础设施，并完成生产部署：

- Markdown / Git knowledge ingestion；
- Persistent SQLite + sqlite-vec index；
- Vector / Lexical / Hybrid / Reranker evaluation；
- Evidence-Grounded Generation + Citation Validation；
- Public API + SSE + Vue Web UI；
- Nginx / HTTPS / systemd / backup / sync；
- `https://ask.zglab.fun` 已完成公网验收。

项目的长期目标随后从“公网 Personal Knowledge Assistant”扩展为：

> **以个人身份为主体的 ZGLab Personal AI Agent。**

未来 Agent 将逐步具备三类能力：

1. **Personal Knowledge Skill**：复用现有 Evidence-Grounded RAG；
2. **Web Research Skill**：从公网检索、抓取、抽取并生成可验证 External Evidence；
3. **MCP Tool Runtime**：通过 MCP 调用经过批准的确定性工具。

由于服务暴露在公网，且服务器、LLM、Search API 与后续 Agent 多步调用成本均由项目所有者承担，
在增加任何新的 cost-bearing capability 之前，必须先建立身份认证、访问控制、Session 吊销与用户级额度边界。

因此在 2026-08-25 对 Phase 11+ 重新基线。

## 2. 当前权威 Roadmap

```text
Phase 0–10  Personal Knowledge Assistant Foundation     ✅ 已完成

Phase 11    Authentication & Access Control             ✅ 已完成并生产验收
Phase 12A   Capability Foundation & PersonalKnowledgeSkill ✅ 已实现
Phase 12B   Web Research Core                           ✅ 已实现
Phase 12C   Evidence + Grounded Generation Integration  ⏳ 下一 Product Phase
Phase 13    MCP Tool Runtime
Phase 14    Agent Orchestrator
Phase 15    Session Context
Phase 16    Owner Agent / Advanced Permissions
```

Phase 11 已于 **2026-08-26** 完成生产迁移、真实 HTTPS 浏览器验收与运维验收并正式封板。
实现与设计见 `docs/authentication.md`、`docs/api-v2.md`；本地验收见
`docs/evaluations/phase-11-authentication-acceptance.md`；生产部署实录见
`docs/phase-11-production-deployment-2026-08-26.md`；最终生产封板证据见
`docs/evaluations/phase-11-production-acceptance-2026-08-26.md`。

Phase 12A（Capability Foundation & PersonalKnowledgeSkill）已实现，见
`docs/capability-architecture.md` 与
`docs/evaluations/phase-12a-capability-foundation.md`；Phase 12B（Web
Research Core）已实现，见 `docs/web-research-runtime.md` 与
`docs/evaluations/phase-12b-web-research-core.md`；Phase 12C（Evidence +
Grounded Generation Integration）未开始，在获得明确授权前不得开始。

Evaluation 继续作为跨阶段基础设施，不重新成为独立 Phase。

Post-v1 性能优化（Reranker 量化、cache、monitoring、latency、evaluation expansion 等）继续保持
非编号优化轨道，不占用上述 Product Capability Phase 编号。

## 3. Phase 11 — Authentication & Access Control

> **封板状态：2026-08-26 已完成生产部署与验收。除安全修复、运维修复和必要兼容性修复外，不再扩展 Phase 11 功能范围。**

### 目标

在 Web Research、MCP 与 Agent Runtime 之前建立统一 Security Foundation。

产品访问模型调整为：

```text
ask.zglab.fun
      │
      ├── Public Landing / Project Showcase
      │       └── 不触发外部 LLM / Search / MCP 消费
      │
      └── Authenticated Application
              └── 当前 RAG + future Agent capabilities
```

### 冻结原则

- **No public registration**：不存在匿名 `/register` / `/signup`；
- 账号只能由管理员通过服务器 CLI 创建和下发；
- 优先使用 **Admin Provisioning + Single-use Activation Token**；
- 密码使用成熟的 **Argon2id** 实现；
- 使用 **Server-side Session + Secure HttpOnly Cookie**，不以 JWT + localStorage 作为默认方案；
- Auth 数据使用独立 `auth.db`，不写入 `knowledge.db`；
- Session / Activation Token 在数据库中只保存 hash，不保存明文；
- AuthN / AuthZ / quota 必须在 Agent Runtime 之外由服务端强制执行；
- Cookie Session 需要 CSRF / Origin 防护；
- Login 需要 per-IP + per-identity throttling；
- 消费型接口需要 per-user rate limit / daily quota；
- 保留 concurrency、timeout、request-size、安全错误等 Phase 9 已有防护；
- `/health` / `/ready` 与公开展示页可以匿名；
- Phase 11 不实现 Web Research、MCP、Agent Planner 或 Conversation Memory。

### API 演进

Public API v1 是 Phase 9 的历史冻结契约，不重写其历史定义。

Phase 11 已落地 authenticated `/api/v2` 契约：

```text
POST /api/v2/auth/login
POST /api/v2/auth/logout
GET  /api/v2/auth/me
POST /api/v2/auth/activate
POST /api/v2/auth/reset-password
POST /api/v2/auth/change-password
POST /api/v2/ask
POST /api/v2/ask/stream
```

旧 `/api/v1/ask` / `/api/v1/ask/stream` 在生产已退役为 `410 API_RETIRED`，
不再作为匿名 LLM 消费入口。

### 子阶段

```text
11A Identity Core
    auth.db / user / password / admin CLI / activation

11B Session Authentication
    login / logout / me / secure cookie / revoke / CSRF / login throttling

11C Protected API & Cost Boundary
    authenticated ask/SSE / authorization / per-user rate limit / daily quota

11D Security & Product Acceptance
    audit / kill switch / public landing / deployment migration / regression
```

## 4. Phase 12 — Agent Capability Foundation & Web Research

原先在 2026-08-21 冻结为 “Phase 11 — External Research & Session Evidence” 的 Web Research
技术设计 **不作废**，而是顺延到 Phase 12。详细边界仍见 `docs/web-research-skill.md`。

### Phase 12A — Capability Foundation & PersonalKnowledgeSkill ✅

已实现：

- 把现有 RAG 抽象为 `PersonalKnowledgeSkill`（wrap，不重写）；
- 建立最小 Capability / Skill contract（CapabilityRequest / Context / Result / Registry）；
- API v2 经 Capability boundary 调用，公开响应与 SSE 契约不变。

验收见 `docs/evaluations/phase-12a-capability-foundation.md`。

### Phase 12B — Web Research Core ✅

已实现：SearchProvider（Tavily adapter + deterministic fake）、candidate
selection、Safe Fetch（SSRF / DNS / redirect 逐跳重验、bounded size /
timeout / content-type）、确定性 extraction、ExternalEvidence（untrusted、
URL provenance）与 bounded ResearchService / WebResearchSkill。未接入
公网 API，未接 Grounded Generation；验收见
`docs/evaluations/phase-12b-web-research-core.md`。

Phase 12B 已完成的要点：

- 实现 request-scoped `WebResearchSkill`；
- SearchProvider 可替换；
- Search → candidate selection → safe fetch → extraction → normalization；
- External Evidence 继续进入 Grounded Generation + Citation Validation；
- Web URL 必须来自系统真实检索结果；
- Prompt Injection 与 SSRF 边界必须在第一版成立；
- Web Research 不写入长期 Personal Knowledge。

### Phase 12 明确不做

- Session Evidence Reuse；
- Full Conversation Memory；
- Autonomous Agent Loop；
- MCP Tool Runtime；
- arbitrary browser/tool use。

原设计里的 Session Evidence 原则保留为参考，但实际 Session Runtime 统一迁移到 Phase 15。

## 5. Phase 13 — MCP Tool Runtime

目标：让 Agent 能通过 MCP 调用受控、确定性、低风险工具。

优先从 `zglab-tools` 中选择适合机器调用的纯逻辑能力，而不是机械 MCP 化全部工具。

第一批候选包括：

- JSON format / validate；
- timestamp conversion；
- Base64 / URL codec；
- UUID generation；
- text count / cleanup / deduplicate / sort；
- regex utility；
- JWT decode；
- hash calculation；
- naming conversion；
- DOI / citation conversion；
- token estimation。

建议结构为：

```text
Browser UI ──┐
             ├── Shared Tool Core
MCP Server ──┘
```

生产初版 MCP Server 应优先作为 localhost/internal capability，而不是直接暴露公网。

## 6. Phase 14 — Agent Orchestrator

在 PersonalKnowledgeSkill、WebResearchSkill 与 MCP Tool Runtime 都稳定后，再建立真正的
Agent Control Plane：

```text
Agent Runtime
├── Capability Registry
├── Router / Planner
├── Policy Engine
├── Bounded Executor
├── Observation Model
└── Final Synthesis / Validation
```

第一版采用 **Bounded Planner + Executor**，而不是无限 ReAct loop。

需要限制 max steps、research count、tool calls、deadline 与 capability permissions。

Agent 根据问题选择：

- Personal Knowledge；
- Web Research；
- MCP Tool；
- 或受控的多能力组合。

## 7. Phase 15 — Session Context

Session Context 与 Personal Knowledge 必须保持概念隔离：

```text
Personal Knowledge      = reviewed, long-lived
Web Evidence            = temporary evidence
Session Context         = temporary conversation state
Long-term Agent Memory  = separate future concern
```

Phase 15 才实现：

- limited conversation reference context；
- temporary web evidence reuse；
- tool artifact reuse；
- lightweight ephemeral session store；
- TTL / max sessions / max items / max bytes。

单实例初版不因为 Session Context 自动引入 Redis。

## 8. Phase 16 — Owner Agent / Advanced Permissions

公网普通用户只允许安全、低副作用 capability。

未来 Owner Agent 才考虑：

- authenticated owner-only capabilities；
- private sources；
- GitHub write；
- file/deploy/admin operations；
- human confirmation / step-up authentication；
- destructive-operation policy。

MCP Tool annotation 只能作为 hint；真正权限仍由 Agent Host / Policy Engine 强制执行。

## 9. 文档优先级与历史记录

### 当前设计文档

以下文档应与本 Roadmap 保持一致：

- `README.md`
- `AGENTS.md`
- `docs/development-plan.md`
- `docs/architecture.md`
- `docs/web-research-skill.md`
- Phase 11 authentication / API v2 / production acceptance 文档

### 历史验收记录

以下类型文件记录当时真实发生的阶段状态，不因为 Roadmap v2 而重写历史：

```text
docs/evaluations/phase-7-*.md
docs/evaluations/phase-9-*.md
docs/evaluations/phase-10-*.md
```

`docs/evaluations/phase-11-authentication-acceptance.md` 记录 2026-08-25 本地封装完成、
生产尚未迁移时的真实状态；2026-08-26 的生产迁移与最终封板由独立生产验收文档记录。

如果历史验收记录写有当时的 “future Phase 11”，它只表示当时的规划，不再具有当前 Roadmap 权威性。

### Public API v1

`docs/public-api.md` 首先是 Phase 9 Public API v1 的冻结记录。
其中任何旧的 future-phase 编号都不改变 v1 已冻结的 endpoint / response / SSE 事实；
未来 authenticated API 通过 Phase 11 的新版本契约演进。

## 10. Codex 执行规则

任何 Codex / Coding Agent 开始 Phase 11+ 任务前必须：

1. 阅读 `AGENTS.md`；
2. 阅读本文档；
3. 将本文档视为 Phase 11+ 的 Roadmap authority；
4. 不根据历史文档中的旧 Phase 编号提前实现功能；
5. 每次只实现当前 Phase 或明确授权的垂直切片；
6. 如发现文档冲突，先报告冲突，不自行混合两个 Phase。

当前唯一允许开始的下一 Product Phase 是：

> **Phase 12C — Evidence + Grounded Generation Integration**（Phase 12 的
> 12A — Capability Foundation 与 12B — Web Research Core 已完成；前提：
> 获得明确授权）
