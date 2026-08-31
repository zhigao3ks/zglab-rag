# ZGLab Personal AI Agent

`zglab-rag` 已从最初的 Personal Knowledge Assistant 演进为 **ZGLab Personal AI Agent**。

生产地址：`https://ask.zglab.fun`

当前系统以个人公开知识为核心，在统一的安全边界下组合：

```text
Personal Knowledge RAG
+ Web Research
+ MCP Tool Runtime
+ Bounded Agent Orchestrator
```

系统可以采用第一人称表达，但任何事实性陈述必须由可追溯、允许公开的 Evidence 支撑。
Persona 只影响表达方式，不允许覆盖事实边界。

## 当前生产状态

```text
Phase 0–10  Personal Knowledge Assistant Foundation        ✅ SEALED
Phase 11    Authentication & Access Control                ✅ SEALED
Phase 12    Capability Foundation & Web Research           ✅ SEALED
Phase 13    MCP Tool Runtime                               ✅ SEALED
Phase 14    Agent Orchestrator                              ✅ COMPLETE / PRODUCTION ACCEPTED / SEALED

UX Track     Frontend / Product Experience Stabilization   ← IMMEDIATE

Phase 15    Conversation & Session Memory                   ← NEXT PRODUCT PHASE
Phase 16    Retrieval Intelligence & Knowledge Graph
Phase 17    Agent Analyst
Phase 18    Advanced Agent Autonomy / Bounded ReAct
Phase 19    Owner Agent / Advanced Permissions
```

自 2026-08-25 起，Phase 11+ 的编号与执行顺序以 [`docs/roadmap-v2.md`](docs/roadmap-v2.md) 为准。
2026-08-28 Phase 14 完成生产验收并封板；后续优先级为：

```text
先让它好用
→ 再让它记得住
→ 再让它更会找
→ 再让它更会分析
→ 最后才让它更自主
```

## 当前能力

### Personal Knowledge

- Markdown / Local Git knowledge ingestion；
- `config/sources.yaml` 驱动的公开知识源注册；
- structure-aware chunking 与稳定 document/chunk ID；
- BGE Embedding benchmark；
- SQLite + `sqlite-vec` persistent index；
- Vector / Lexical / Hybrid / Reranker evaluation；
- Evidence-Grounded Generation；
- claim-level Citation Validation；
- `visibility=public` 服务端强制边界。

### Web Research

```text
Search
→ deterministic candidate selection
→ SSRF / DNS / redirect validation
→ pinned safe fetch
→ extraction
→ ExternalEvidence
→ Grounded Generation
→ Citation Validation
```

Web evidence 始终视为 **untrusted data**，只在 request scope 内使用，不写入长期 Personal Knowledge；
最终 URL 只能来自服务端 provenance。

### MCP Tool Runtime

跨仓库边界：

```text
zglab-tools Shared Tool Core
        ↓
TypeScript MCP Server
        ↓ stdio
Python MCP Client
        ↓
zglab-rag MCPToolRuntime
```

当前第一批工具为 deterministic / side-effect-free 工具，包括 JSON、Base64、URL、文本处理与时间戳转换。
生产使用独立 Node 22 runtime，MCP 仅 internal / stdio，不开放公网 endpoint。

### Agent Orchestrator

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
Final Synthesis
    ↓
AgentAnswer
```

当前第一版 Agent 刻意保持 bounded：

```text
max steps = 4
Personal <= 1
Web <= 1
MCP <= 3
sequential executor
deterministic planner
no automatic retry
no replanning
no infinite ReAct
```

关键不变量：

- **Evidence before Persona**；
- **ToolResult != Evidence**；
- Web Evidence 是 untrusted data；
- MCP Host allowlist 才是工具授权边界；
- Planner proposes, Executor enforces；
- Auth / quota / concurrency / kill switch 位于 Agent Runtime 外部。

Phase 14 的生产封板证据见 [`docs/evaluations/phase-14-production-acceptance-2026-08-28.md`](docs/evaluations/phase-14-production-acceptance-2026-08-28.md)。

## Authentication & Cost Boundary

公网产品采用“公开展示 + 登录后使用消费型 AI 能力”的访问模型：

```text
Public Landing
    ↓
Login
    ↓
Authentication / Authorization / CSRF
    ↓
Quota / Concurrency / Kill Switch
    ↓
Personal / Web / Agent Runtime
```

冻结原则：

- 不开放 public signup；
- 管理员 CLI provisioning + single-use activation；
- Argon2id；
- server-side opaque session；
- Secure HttpOnly Cookie；
- CSRF / Origin validation；
- per-IP / per-identity throttling；
- per-user / per-capability quota；
- `knowledge.db` 与 `auth.db` 生命周期独立；
- 登录不等于 private knowledge 自动开放；
- 历史匿名 `/api/v1` 消费入口已退役。

## 当前 UX Track

Phase 15 暂不开始。当前最高优先级是现有 Vue 聊天体验稳定化：

- viewport-bounded application shell；
- independent message scroll container；
- fixed/sticky composer experience；
- send / completed 自动定位最新消息；
- SSE near-bottom smart follow；
- 用户主动上翻时停止强制跟随；
- “回到最新消息”；
- Header / Navigation viewport 与 responsive 修复；
- 为未来 Session Sidebar 预留 workspace 结构。

本 UX Track 不改变 Agent / RAG / Auth / Web / MCP 后端语义。

## 主要文档

- [`docs/roadmap-v2.md`](docs/roadmap-v2.md)：Phase 11+ 权威路线图
- [`docs/architecture.md`](docs/architecture.md)：当前系统与演进架构
- [`docs/production-architecture.md`](docs/production-architecture.md)：Phase 14 封板后的生产架构
- [`docs/development-plan.md`](docs/development-plan.md)：开发阶段与当前 UX Track
- [`docs/authentication.md`](docs/authentication.md)：Phase 11 安全基础
- [`docs/capability-architecture.md`](docs/capability-architecture.md)：Capability Foundation
- [`docs/web-research-runtime.md`](docs/web-research-runtime.md)：Web Research Core
- [`docs/web-evidence-grounding.md`](docs/web-evidence-grounding.md)：Web Evidence Grounding
- [`docs/mcp-tool-runtime.md`](docs/mcp-tool-runtime.md)：MCP Tool Runtime 边界
- [`docs/agent-architecture.md`](docs/agent-architecture.md)：Phase 14 Agent 架构
- [`docs/agent-product.md`](docs/agent-product.md)：Phase 14D 产品接入
- [`docs/evaluations/phase-14-production-acceptance-2026-08-28.md`](docs/evaluations/phase-14-production-acceptance-2026-08-28.md)：Phase 14 生产封板

历史 acceptance / evaluation 文档记录当时状态，不因后续 Roadmap 调整而改写。

## 目录

```text
zglab-rag/
├── AGENTS.md
├── README.md
├── config/
├── deploy/
├── docs/
├── evaluation/
├── knowledge/
├── src/zglab_rag/
│   ├── agent/
│   ├── api/
│   ├── application/
│   ├── auth/
│   ├── capabilities/
│   ├── domain/
│   ├── embeddings/
│   ├── evaluation/
│   ├── generation/
│   ├── indexing/
│   ├── ingestion/
│   ├── mcp/
│   ├── research/
│   ├── retrieval/
│   ├── reranking/
│   ├── sources/
│   └── storage/
├── tests/
└── web/
```

## 本地开发

默认环境：WSL2 Ubuntu 24.04 + Python 3.12 + `uv`。

```bash
uv sync
uv run pytest
uv run ruff check .
uv run uvicorn zglab_rag.api.main:app --reload
```

Web：

```bash
cd web
npm install
npm run test:run
npm run build
npm run dev
```

真实 API Key、Session Secret、数据库、模型 cache、日志与运行时数据不得提交 Git。
