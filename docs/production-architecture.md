# 当前生产架构（Phase 14 封板后）

> 当前状态：Phase 11–14 均已完成并生产封板。本文描述 `https://ask.zglab.fun` 在 Phase 14 验收后的生产形态。权威 Roadmap 见 `docs/roadmap-v2.md`，Phase 14 封板证据见 `docs/evaluations/phase-14-production-acceptance-2026-08-28.md`。

## 1. 当前拓扑

```text
Internet
   ↓ HTTPS
ask.zglab.fun
   ↓
Nginx
   ├── Vue SPA                     /var/www/zglab-assistant
   └── FastAPI / SSE               127.0.0.1:8000
          ↓
      Security Boundary
      ├── Origin / AuthN / AuthZ / CSRF
      ├── request size / timeout
      ├── global concurrency
      ├── per-user / per-capability quota
      ├── Web research concurrency
      ├── Agent concurrency
      └── capability kill switches
          ↓
      Product Routing
      ├── Auto / Personal
      │      └── PersonalKnowledgeSkill
      ├── Web
      │      └── WebResearchSkill
      └── Agent
             └── Bounded Agent Runtime
                  ├── PersonalKnowledgeSkill
                  ├── WebResearchSkill
                  └── MCPToolRuntime
```

生产消费型入口以 authenticated API v2 为准：

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

历史匿名 `/api/v1/ask` 与 `/api/v1/ask/stream` 已退役并返回 `410 API_RETIRED`。

## 2. 服务器目录

```text
/opt/zglab-rag/
├── app/                         # 本仓库 + .venv
│   ├── config/sources.yaml
│   ├── knowledge/
│   └── deploy/
├── notes/                       # 已注册 Git 来源
├── zglab-website/               # 已注册 Git 来源
├── zglab-tools/                 # 已注册 Git 来源 + MCP runtime artifact 来源
├── resume-tailor-agent/         # 已注册 Git 来源
├── zglab-daily/                 # 已注册 Git 来源
├── runtime/
│   ├── knowledge.db             # Personal knowledge / index
│   ├── auth.db                  # identity / session / audit / quota，schema v4
│   ├── backups/
│   └── logs/
├── rollback/
├── models/
│   └── huggingface/
└── .env                         # 仅服务器保存 secrets

/var/www/zglab-assistant/        # Vue 构建产物
```

`knowledge.db` 与 `auth.db` 是独立 lifecycle。Phase 14 后 `auth.db` schema v4 包含既有 `web_usage` 与独立 `agent_usage`。

## 3. API 服务与安全顺序

`zglab-rag-api.service` 以 `zglab` 用户运行 Uvicorn，仅监听 `127.0.0.1:8000`，由 Nginx 对外提供 HTTPS。

Authenticated ask / SSE 的安全顺序必须保持在 Agent Runtime 外部：

```text
Request size / schema validation
→ Origin
→ Authentication
→ Authorization
→ CSRF
→ LLM / capability kill switch
→ Question controls
→ product mode / server-side policy
→ global concurrency
→ capability-specific concurrency
→ capability-specific quota
→ Personal / Web / Bounded Agent Runtime
```

关键不变量：

- 匿名请求在 capability 状态暴露前被拒绝；
- 客户端只能使用冻结的产品 mode，不得传任意内部 capability id；
- capability enablement / allowlist / quota 由服务端强制；
- Agent 不拥有绕过 Auth、quota、concurrency 或 kill switch 的能力；
- optional capability 故障不应无条件拉死 Personal RAG readiness。

## 4. Personal Knowledge Path

```text
PersonalKnowledgeSkill
→ request-scoped read-only knowledge.db
→ vector retrieval（public-only）
→ Evidence Context
→ Grounded Generation
→ Citation Validation
→ Answer + personal sources
```

登录不自动开放 private knowledge；生产检索继续强制 `visibility=public`。

## 5. Web Research Path

```text
WebResearchSkill
→ Tavily SearchProvider
→ deterministic candidate selection
→ URL / DNS safety validation
→ PinnedResolutionBackend
→ bounded Safe Fetch
→ deterministic extraction
→ ExternalEvidence（origin=web, trust=untrusted）
→ Grounded Generation
→ Citation Validation
→ provenance-backed web sources
```

安全边界：

- 只允许 http / https；
- loopback、RFC1918、link-local、metadata、CGNAT、unsafe IPv6 等地址拒绝；
- DNS / redirect 每跳重验并重新 pin；
- TLS hostname verification / SNI / Host 保持原 hostname；
- response size、timeout、redirect count、content type、extracted chars 均 bounded；
- Web evidence 是 untrusted data，不能成为 system/tool instruction；
- 最终 source URL 由服务端 provenance 映射；
- ExternalEvidence 不写入长期 `knowledge.db`。

## 6. MCP Tool Runtime

生产 MCP 采用 internal stdio，不开放公网 MCP endpoint：

```text
zglab-tools Shared Tool Core
→ TypeScript MCP Server
→ Node 22 runtime
→ stdio
→ Python MCP Client
→ MCPToolRuntime
```

Phase 14 生产 smoke 暴露过长生命周期 MCP connection 的 event-loop ownership 问题；最终修复为 request-scoped MCP host runtime 并完成生产验证。

工具安全边界：

- Host allowlist 才是授权边界；
- 工具第一版 deterministic / side-effect-free；
- ToolResult 不产生 Evidence / source / citation；
- 工具 raw data 不通过 SSE 暴露。

## 7. Agent Path

```text
mode=agent
   ↓
Agent Security / Quota Boundary
   ↓
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

生产冻结预算：

```text
max steps = 4
Personal <= 1
Web <= 1
MCP <= 3
overall deadline
no automatic retry
no replanning
no infinite ReAct
```

核心策略：

- Planner proposes, Executor enforces；
- Executor 重复校验预算与 deadline；
- Web observation 仍为 untrusted evidence；
- Web content 不能修改冻结 plan；
- ToolResult != Evidence；
- 单 Personal/Web 优先复用已有 grounded result；
- 多能力结果才进入 final synthesis。

当前生产 `AGENT_ENABLED=true`，Phase 14 已完成真实 Personal、Web、Tool 与 multi-capability smoke。

## 8. SSE

Personal：

```text
accepted → retrieving → generating → validating → completed
```

Web：

```text
accepted → researching → generating → validating → completed
```

Agent：

```text
accepted → planning → executing → synthesizing → validating → completed
```

SSE 不发送：

- Agent plan；
- AgentObservation；
- tool arguments / raw result；
- 网页正文；
- Prompt；
- Evidence raw payload；
- chain-of-thought / reasoning。

## 9. Nginx 与浏览器边界

`deploy/nginx/ask.zglab.fun.conf` 提供：

- Vue history fallback；
- API / SSE reverse proxy；
- `proxy_buffering off` for SSE；
- HTTPS；
- CSP；
- `X-Content-Type-Options: nosniff`；
- `X-Frame-Options: SAMEORIGIN`；
- strict referrer policy。

Session 使用 server-side opaque token + Secure HttpOnly host-only cookie；SPA 只持有 session-bound CSRF token。

Web source 与 LLM 输出均通过 Vue text binding 渲染，不使用 `v-html`；外链使用安全 provenance URL。

## 10. 配置与 Secrets

生产配置位于 `/opt/zglab-rag/.env`，不进入 Git。

配置包括：

- LLM；
- Auth / Session；
- Web Research / Search Provider；
- MCP runtime；
- Agent enablement；
- quota / concurrency；
- API retirement；
- runtime paths。

真实 API Key、Session Secret、Activation Token 等不得进入仓库、日志、验收文档或 API response。

## 11. 备份与恢复

`zglab-rag-backup.timer` 继续同时备份：

```text
knowledge.db
+ auth.db
```

Phase 14 部署前后双库均已完成 integrity check，auth schema v3 → v4 使用正式 migration；最终 backup service 验证成功。

恢复原则：

1. 停止 API；
2. 保留当前数据库副本；
3. 恢复经过 `PRAGMA integrity_check=ok` 的快照；
4. 校正 ownership / permission；
5. 再启动服务并验证 health / ready / auth / capability smoke。

## 12. 知识同步

`zglab-rag-sync.timer` 负责已注册 Git knowledge sources 的 fast-forward-only 同步与增量索引。

远端不可达、checkout 不干净、解析 / Embedding 失败时不得破坏上一版 `knowledge.db`。
Web Research、Session、Agent observation 与 ToolResult 均不应被同步任务自动写入 Personal Knowledge。

## 13. 部署与回滚

生产升级继续遵循：

1. 记录当前 commit 与 service 状态；
2. 创建 app / frontend / config / 双库 rollback snapshot；
3. `git pull --ff-only` + `uv sync --frozen`；
4. 执行正式 schema migration；
5. build / 发布 Vue；
6. `nginx -t` + reload；
7. restart API；
8. health / ready / Auth / Personal regression；
9. Web / MCP / Agent 受控 smoke；
10. 验证 quota / concurrency / kill switch；
11. 手动 backup + integrity check。

快速 capability rollback 应优先使用独立 kill switch，而不是破坏其他能力。

## 14. 当前运维状态

Phase 14 最终生产验收记录：

```text
zglab-rag-api.service      active
nginx                      active
zglab-rag-backup.timer     active
zglab-rag-sync.timer       active
/health                    200
/ready                     200
AGENT_ENABLED              true
```

核心 Agent evaluation gate：

```text
budget_violation_rate = 0
unauthorized_tool_execution = 0
```

## 15. 下一步

下一工作项不是 Phase 15，而是非编号 **UX Track — Frontend / Product Experience Stabilization**。

该 Track 只优化当前 Vue chat shell、message scroll、composer、smart follow、responsive layout，并为未来 Session Sidebar 预留结构；不改变任何已封板后端语义。

UX Track 完成后才进入 Phase 15 Conversation & Session Memory。
