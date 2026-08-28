# ZGLab RAG

ZGLab RAG 已从最初的 Personal Knowledge Assistant 演进为 **ZGLab Personal AI Agent** 的知识与安全基础。

当前生产版本已经完成以个人公开身份、项目、技术知识与实践经验为知识基础的 Evidence-Grounded RAG，
并部署在：

- `https://ask.zglab.fun`

系统回答可以采用第一人称，但任何事实性陈述都必须由可追溯、允许公开的 Evidence 支撑。
Persona 只影响表达方式，不允许覆盖事实边界。

## 当前能力

Phase 0–11 已完成：

- Markdown / Local Git knowledge ingestion；
- `config/sources.yaml` 驱动的公开知识源注册；
- 结构感知 Chunking 与稳定 document/chunk ID；
- BGE Embedding benchmark；
- SQLite + `sqlite-vec` 持久化 Vector Index；
- FTS5 lexical retrieval 与 RRF Hybrid evaluation；
- CrossEncoder Reranker evaluation；
- Evidence-Grounded Generation；
- claim-level Citation Validation；
- Public API v1（Phase 9 冻结，Phase 11 起由 v2 接替）；
- SSE 状态流；
- Vue 3 Web Assistant；
- Nginx / HTTPS / systemd；
- 增量 Git source sync；
- SQLite 原子备份与恢复；
- Phase 9 / Phase 10 产品与生产验收；
- **Phase 11 Authentication & Access Control**：独立 `auth.db`、Argon2id、
  admin CLI 开户 + 单次激活链接、Server-side Session + HttpOnly Cookie、
  CSRF / Origin 防护、登录双维度限流、用户级配额、LLM kill switch、
  `/api/v2` 认证问答与 v1 退役。

当前生产索引约 1,058 个 Chunk，`knowledge.db` 约 9.6 MiB。生产环境稳定 RSS 约 439 MiB，
API / SSE / Vue SPA / Sources / insufficient-evidence 均已完成公网验证。

## 核心原则

1. **Evidence before Persona**：先证据，后第一人称表达。
2. **Public boundary first**：当前知识检索始终强制 `visibility=public`。
3. **Source registry driven**：正式知识源只从 `config/sources.yaml` 获取。
4. **Replaceable AI components**：Embedding、Vector、Lexical、Reranker、LLM 通过独立边界接入。
5. **Evaluation before optimization**：算法替换必须有可比较评测。
6. **Runtime data is not source code**：数据库、模型、缓存、日志、Secret 不进入 Git。
7. **Security before capability expansion**：新增 Web Research / MCP / Agent 之前先建立认证、授权和成本边界。

## Roadmap v2

> **重要：自 2026-08-25 起，`docs/roadmap-v2.md` 是 Phase 11+ 的权威路线图。**
>
> 仓库历史上曾把 Web Research 冻结为 Phase 11。该技术设计本身仍有效，但编号已经顺延。
> Phase 0–10 的历史编号、实现与验收不变。

```text
Phase 0–10  Personal Knowledge Assistant Foundation     ✅ 已完成
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

Phase 14A–14C 已建立 contracts、deterministic bounded planner 及内部 sequential executor/final
synthesis：单能力复用既有 grounded result，多能力才调用 injected synthesis；无 ReAct、replan、retry
或产品入口。Phase 14 已完成内部与生产验收；下一阶段为 Phase 15 Session Context。

完整新路线见 [`docs/roadmap-v2.md`](docs/roadmap-v2.md)。

## Phase 11 — Authentication & Access Control（已完成并生产验收）

访问模型为“展示公开，消费型 AI 能力登录后使用”：

```text
ask.zglab.fun
      │
      ├── Public Landing / Project Showcase
      │       └── 不触发外部 LLM / Search / MCP 消费
      │
      └── Authenticated Application
              └── RAG + future Agent capabilities
```

已落地的冻结原则：

- 不开放匿名注册；账号由管理员 CLI 创建和下发；
- Admin Provisioning + Single-use Activation Token（数据库只存 SHA-256）；
- Argon2id password hashing（长度优先策略，12–128）；
- Server-side Session + `__Host-zglab_session` Secure/HttpOnly/SameSite=Lax Cookie；
- Auth 数据独立于 `auth.db`（schema version 2，WAL，fail-fast），不写入 `knowledge.db`；
- Origin validation + session-bound CSRF token（SSE 与普通 ask 同一安全门）；
- per-IP + per-username login throttling；统一登录错误防枚举；
- per-user rate limit / daily quota（429 + 审计）；
- `ZGLAB_RAG_LLM_ENABLED` kill switch；`ZGLAB_RAG_API_V1_RETIRED` 退役开关；
- 保留 Phase 9 的 concurrency / timeout / request-size / safe-error 防护；
- Public Landing 与 `/health` / `/ready` 保持匿名可访问；
- 未实现 Web Research、MCP、Agent Planner、Conversation Memory（后续 Phase）。

管理员 CLI（无 Web Admin Console）：

```bash
zglab-rag auth init
zglab-rag user create <username> [--role ADMIN|USER]   # 输出一次性激活链接
zglab-rag user list / show / disable / enable
zglab-rag user reset-password <username>               # 撤销会话 + 一次性重置链接
zglab-rag user revoke-sessions <username>
zglab-rag backup --auth
```

设计细节：[`docs/authentication.md`](docs/authentication.md)、
[`docs/api-v2.md`](docs/api-v2.md)；验收：
[`docs/evaluations/phase-11-authentication-acceptance.md`](docs/evaluations/phase-11-authentication-acceptance.md)。

## Phase 12A — Capability Foundation（已实现）

现有 RAG 管线被封装为第一个受控能力 `PersonalKnowledgeSkill`，API v2 通过
Capability boundary 调用它（wrap，不重写；公开响应与 SSE 契约零变化）：

- 最小 Capability contract：`CapabilityRequest` 只含 `question`，客户端无法
  控制 retrieval / visibility / provider；
- `CapabilityResult` 区分 SUCCESS / INSUFFICIENT_EVIDENCE / FAILED，保留
  Phase 8 “证据不足是业务结果不是异常”语义；
- `CapabilityRegistry` 只做确定性注册/查找，不是 Planner；当前只注册
  `personal_knowledge`；
- AuthN / AuthZ / CSRF / quota / concurrency / kill switch 全部保持在
  Capability 之前的安全门；登录（含 ADMIN）不解锁 private knowledge；
- 未实现 Web Research / MCP / Planner（Phase 12B+ / 13 / 14）。

设计：[`docs/capability-architecture.md`](docs/capability-architecture.md)；验收：
[`docs/evaluations/phase-12a-capability-foundation.md`](docs/evaluations/phase-12a-capability-foundation.md)。

## Phase 12B — Web Research Core（已实现）

独立的 bounded Web Research 管线（未接入公网 API，未接 Grounded Generation）：

```text
Query → SearchProvider（Tavily，每次恰好 1 次调用）
     → deterministic candidate selection
     → Safe Fetch（scheme/userinfo/DNS/IP 逐跳 SSRF 重验、redirect 上限、
        bounded 解压字节、content-type 白名单、无 cookie/凭证）
     → 确定性 HTML 抽取（无 LLM）
     → ExternalEvidence[]（W1…Wn，origin=web，trust=untrusted，URL provenance）
```

- 失败模型区分 NO_RESULTS / NO_USABLE_EVIDENCE / PROVIDER_UNAVAILABLE /
  TIMEOUT / TECHNICAL_FAILURE / POLICY_DISABLED；技术故障不等于“知识不存在”；
- `ZGLAB_RAG_WEB_RESEARCH_ENABLED` 默认 false（fail-closed，至 12D 验收）；
  Search API Key 只经环境变量；
- 未实现：Planner / MCP；回答生成与 citation
  集成已在 12C 完成，产品接入（researching SSE / mode）在 12D 完成（见下）。

运行时设计：[`docs/web-research-runtime.md`](docs/web-research-runtime.md)；验收：
[`docs/evaluations/phase-12b-web-research-core.md`](docs/evaluations/phase-12b-web-research-core.md)。

## Phase 12C — Web Evidence + Grounded Generation Integration（已实现）

让 12B 的 `ExternalEvidence[]` 安全进入既有 Evidence-Grounded Generation +
Citation Validation（仅内部能力，`/api/v2/ask` 与 SSE 公网契约零变化）：

```text
ExternalEvidence[]（W1…Wn）
    → web_adapter：W→E 确定性映射，origin=web，不伪造 chunk 身份
    → build_web_context：第三人称 prompt + UNTRUSTED WEB EVIDENCE 标注
    → generate_from_context：与 Personal 共享的 Phase 8 生成管线
    → Citation Validation（validator 零改动）→ CapabilityResult(origin=web)
```

- citation 展示命名空间统一为 `E1/E2`；citation URL 只能来自 provenance，
  LLM 无法创造 source URL；
- 网页内容全程标记为 UNTRUSTED 数据；注入类 fixture 测试锁定 prompt/data
  分离；web 证据不得被声明为用户本人经历（Personal Facts Integrity）；
- zero evidence 不调用 LLM；研究技术故障（FAILED）与业务 insufficient 严格区分；
- PersonalKnowledgeSkill 完整回归，Personal 路径不依赖 SearchProvider。

设计：[`docs/web-evidence-grounding.md`](docs/web-evidence-grounding.md)；验收：
[`docs/evaluations/phase-12c-web-evidence-grounding.md`](docs/evaluations/phase-12c-web-evidence-grounding.md)。

## Phase 12D — Web Research Product Integration（已生产验收/封板）

把 Personal 与 Web 两项能力安全接入 Authenticated API v2。默认配置仍为
fail-closed；生产已完成真实 provider smoke、HTTPS API/SSE、开关/配额/故障
隔离与备份验收，当前 `WEB_RESEARCH_ENABLED=true`：

- 确定性、非 LLM 的 capability selection：`mode = auto/personal/web`；
  自我指涉优先 PERSONAL，时新意图去 WEB，模糊一律 PERSONAL（不浪费搜索成本）；
- API additive：request 可选 `mode`；source 新增 `origin/url/domain`；
  新错误码 `CAPABILITY_DISABLED` / `CAPABILITY_DENIED`；
- SSE：web path 发 `researching`；personal path 不变；
- 独立 web quota（分钟/天）、服务端权限策略、research 并发=1；
- DNS rebinding TOCTOU 由 pinned resolution 关闭（TLS SNI / Host 不变）；
- 前端：mode 选择控件、web source 安全外链（`target=_blank`
  `rel=noopener noreferrer`，无 v-html）、researching 状态。

设计：[`docs/web-research-product.md`](docs/web-research-product.md)；验收：
[`docs/evaluations/phase-12d-product-acceptance.md`](docs/evaluations/phase-12d-product-acceptance.md)；
评估：[`docs/evaluations/phase-12-web-research-evaluation.md`](docs/evaluations/phase-12-web-research-evaluation.md)；
生产封板：[`docs/evaluations/phase-12-production-acceptance-2026-08-28.md`](docs/evaluations/phase-12-production-acceptance-2026-08-28.md)。

## Phase 12+ Agent 方向

长期系统目标不是“RAG + 几个插件”，而是三类能力在统一 Agent Runtime 下组合：

```text
                    ZGLab Personal AI Agent
                              │
                     Agent Control Plane
                              │
             ┌────────────────┼────────────────┐
             │                │                │
             ▼                ▼                ▼
      Personal Knowledge   Web Research      MCP Tools
             │                │                │
             ▼                ▼                ▼
       knowledge.db        Public Web       Tool Runtime
```

- **Phase 12A（已完成）**：把现有 RAG 抽象为 PersonalKnowledgeSkill，建立最小 Capability contract / registry；
- **Phase 12B（已完成）**：SearchProvider / safe fetch / extraction / ExternalEvidence 的 Web Research Core；
- **Phase 12C（已完成）**：External Evidence → 共享 Grounded Generation / Citation Validation（仅内部能力，未接入公网 API）；
- **Phase 12D（已生产验收/封板）**：确定性 Personal/Web 选择、公网 contract（mode/web source/researching SSE）、独立成本边界与 Evaluation；
- **Phase 13**：把适合机器调用的 `zglab-tools` 能力通过 MCP 暴露；
- **Phase 14**：建立 Capability Registry、Router / Planner、Policy Engine、Bounded Executor；
- **Phase 15**：再处理多轮 Session Context、Temporary Evidence Reuse、Tool Artifact Reuse；
- **Phase 16**：Owner-only、private、write / destructive capability 与 step-up confirmation。

Web Research 原冻结设计见 [`docs/web-research-skill.md`](docs/web-research-skill.md)。

## 主要文档

- [`docs/roadmap-v2.md`](docs/roadmap-v2.md)：Phase 11+ 权威路线图
- [`docs/architecture.md`](docs/architecture.md)：Phase 0–10 RAG 基础架构与演进方向
- [`docs/development-plan.md`](docs/development-plan.md)：按 Phase 的开发计划
- [`docs/knowledge-model.md`](docs/knowledge-model.md)：知识模型与 public/private 边界
- [`docs/generation-grounding.md`](docs/generation-grounding.md)：Grounding / Citation 设计
- [`docs/public-api.md`](docs/public-api.md)：Phase 9 Public API v1 冻结记录
- [`docs/authentication.md`](docs/authentication.md)：Phase 11 认证与访问控制设计
- [`docs/api-v2.md`](docs/api-v2.md)：Phase 11 Authenticated API v2 契约
- [`docs/capability-architecture.md`](docs/capability-architecture.md)：Phase 12A Capability Foundation 设计
- [`docs/web-research-runtime.md`](docs/web-research-runtime.md)：Phase 12B Web Research 运行时设计
- [`docs/web-evidence-grounding.md`](docs/web-evidence-grounding.md)：Phase 12C Web Evidence Grounding 设计
- [`docs/web-research-product.md`](docs/web-research-product.md)：Phase 12D Web Research 产品接入与迁移 Runbook
- [`docs/web-research-skill.md`](docs/web-research-skill.md)：Phase 12 Web Research 设计
- [`docs/production-architecture.md`](docs/production-architecture.md)：Phase 10 生产架构
- [`docs/evaluations/phase-10-production-acceptance.md`](docs/evaluations/phase-10-production-acceptance.md)：生产验收记录
- [`docs/evaluations/phase-11-authentication-acceptance.md`](docs/evaluations/phase-11-authentication-acceptance.md)：Phase 11 验收记录

## 目录

```text
zglab-rag/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── config/
├── evaluation/
├── docs/
├── knowledge/
├── web/
├── deploy/
├── src/zglab_rag/
│   ├── api/
│   ├── application/
│   ├── auth/
│   ├── capabilities/
│   ├── research/
│   ├── domain/
│   ├── embeddings/
│   ├── evaluation/
│   ├── generation/
│   ├── indexing/
│   ├── ingestion/
│   ├── retrieval/
│   ├── reranking/
│   ├── sources/
│   └── storage/
└── tests/
```

## 本地开发

建议环境：WSL2 Ubuntu 24.04 + Python 3.12 + `uv`。

```bash
uv sync
uv run pytest -q
uv run ruff check .
uv run uvicorn zglab_rag.api.main:app --reload
```

Web：

```bash
cd web
npm install
npm test -- --run
npm run build
npm run dev
```

## 现有检索与生成

生产默认 Retriever 仍为 `vector`；Reranker 作为显式可选能力保留。

```bash
uv run python -m zglab_rag.retrieval.cli search \
  "Agent 长期记忆和 Context 有什么区别？" --mode vector --top-k 5

uv run python -m zglab_rag.generation.cli ask \
  "你做过哪些 Agent 项目？"
```

LLM 使用 OpenAI-compatible endpoint，真实 Key 只保存在 Git ignored 的 `.env` / 生产环境配置中。

## 生产部署

Phase 10 已完成并验证：

```text
Internet
   ↓ HTTPS
ask.zglab.fun
   ↓
Nginx
   ├── Vue SPA
   └── FastAPI / SSE
          ↓
      local BGE
      SQLite + sqlite-vec
      external LLM
```

systemd API、Nginx、备份 timer、同步 timer 均已投入生产。GitHub 出站网络异常时同步任务会安全失败，
继续使用上一版可用索引提供服务。

## 安全边界

默认禁止进入当前公开知识库：

- 公司内部仓库、客户资料、合同原文、内部接口文档；
- Token、密码、SSH Key、Cookie、API Key；
- 私人聊天与未确认事实；
- 未明确允许公开的 private repository 内容；
- 模型缓存、索引数据库、日志与临时文件。

Phase 11 引入 Authentication **不代表** private knowledge 自动开放。Private / owner-only 能力必须在后续阶段单独设计。
