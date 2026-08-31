# ZGLab Personal AI Agent — 架构设计

> Phase 11+ 的编号与执行顺序以 `docs/roadmap-v2.md` 为准。Phase 0–10 是已完成并封板的 Personal Knowledge Assistant Foundation；Phase 11–14 已完成生产封板。

## 1. 系统定位

ZGLab 当前是一个以个人公开知识为核心、结合受控 Web Research、MCP Tool Runtime 与 Bounded Agent Orchestrator 的 Personal AI Agent。

```text
                    ZGLab Personal AI Agent
                              │
                     Security / Cost Boundary
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

Persona 只影响表达方式，不是事实来源。

## 2. Knowledge Foundation

生产知识路径：

```text
config/sources.yaml
      ↓
Source Adapter
      ↓
RawDocument
      ↓
Normalize / Markdown Parse
      ↓
KnowledgeDocument / Chunk
      ↓
Embedding
      ↓
SQLite metadata + sqlite-vec + FTS5
```

核心不变量：

- 所有正式知识源必须注册；
- include allowlist / exclude 优先；
- stable provenance；
- `visibility=public` 在检索层强制；
- embedding profile 明确版本化；
- source sync failure 不破坏上一版可服务 index。

## 3. Retrieval & Grounded Generation

生产默认 Retriever 仍为 Vector；Lexical / Hybrid / Reranker 保留为可比较替代路径。

```text
Question
  ↓
RetrievalResult[]
  ↓
Evidence Context
  ↓
GenerationProvider
  ↓
claim-level output
  ↓
CitationValidator
  ↓
GroundedAnswer + Sources
```

冻结原则：

- Evidence before Persona；
- Persona 不是 Evidence；
- Evidence 作为只读数据进入 Context；
- LLM 只能引用本次分配的短 Evidence ID；
- Citation validity / ownership / coverage 由确定性代码检查；
- 无足够证据时返回 insufficient evidence。

## 4. Authentication & Security Boundary

Phase 11 已建立并封板：

```text
Internet
   ↓
Nginx
   ↓
Origin / Authentication / Authorization / CSRF
   ↓
Kill Switch / Question Controls
   ↓
Quota / Concurrency
   ↓
Application / Agent Runtime
```

关键边界：

- no public signup；
- server-side opaque session + Secure HttpOnly Cookie；
- `auth.db` 与 `knowledge.db` 生命周期独立；
- login 不等于 private knowledge 开放；
- Auth / quota / concurrency 必须位于 Agent Runtime 外部。

## 5. Capability Architecture

### PersonalKnowledgeSkill

```text
Question
→ public-only retrieval
→ Evidence Context
→ Grounded Generation
→ Citation Validation
→ CapabilityResult
```

### WebResearchSkill

```text
Question
→ SearchProvider
→ deterministic candidate selection
→ SSRF / DNS / redirect validation
→ pinned safe fetch
→ extraction
→ ExternalEvidence
→ Grounded Generation
→ Citation Validation
```

Web evidence：

- request-scoped；
- `origin=web`；
- untrusted；
- 不写入长期 Personal Knowledge；
- URL 只能来自服务端 provenance；
- 网页正文不能成为 system / tool instruction。

### MCPToolRuntime

```text
zglab-tools Shared Tool Core
→ TypeScript MCP Server
→ stdio
→ Python MCP Client
→ MCPToolRuntime
```

工具授权由 Host allowlist 强制，MCP annotation 不是最终授权边界。
Tool result 不被伪装成 Evidence / source / citation。

## 6. Agent Architecture — Phase 14 SEALED

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

### Planner

当前 Planner 以 deterministic fast path 为主，负责提出 bounded plan，不拥有无限执行权限。

### Executor

Executor 再次强制：

```text
max steps = 4
Personal <= 1
Web <= 1
MCP <= 3
overall deadline
```

执行模型：

- sequential；
- no automatic retry；
- no replanning；
- dependency failure fail-bounded；
- no infinite ReAct。

### Observation Model

```text
AgentObservation
├── Personal / Web Capability Observation
│      └── 可保留 Evidence / provenance
└── ToolObservation
       └── structured ToolResult / safe error
```

`ToolResult != Evidence`。

### Synthesis

- 单 Personal/Web 可复用既有 grounded answer；
- 单 Tool 可确定性渲染；
- 多能力计划才进入 final synthesis；
- Web observation 仍是 untrusted evidence；
- Web content 不能修改冻结计划。

## 7. Public Product Boundary

Authenticated API v2 支持：

```text
mode=auto
mode=personal
mode=web
mode=agent
```

`auto / personal / web` 原行为保持冻结；`agent` 通过独立 kill switch、quota 与 concurrency 进入 bounded runtime。

Agent SSE 只公开：

```text
accepted
→ planning
→ executing
→ synthesizing
→ validating
→ completed
```

不得暴露 plan、observation、tool raw data、网页正文、Prompt 或推理。

## 8. Production Architecture

```text
Internet
   ↓ HTTPS
ask.zglab.fun
   ↓
Nginx
   ├── Vue SPA
   └── FastAPI / SSE
          ↓
      Security Boundary
          ↓
      Personal / Web / Agent
          ↓
      knowledge.db / auth.db
      local BGE / external LLM
      Tavily / MCP Node runtime
```

详细生产拓扑与部署边界见 `docs/production-architecture.md`。

## 9. Frontend / UX Track

Phase 14 封板后先执行 UX Track，不立即进入 Session Memory。

目标结构：

```text
Assistant Layout
├── future Session Sidebar slot
└── Workspace
    ├── Header / Navigation
    └── Chat Area
        ├── independent Message Scroll
        └── Composer Dock
```

本 Track 只改变前端布局和滚动体验，不改变 API / SSE / Agent / RAG / Auth / Web / MCP 语义。

## 10. Future Architecture

### Phase 15 — Conversation & Session Memory

```text
Conversation persistence
+ bounded multi-turn context
+ context compression
+ session resource reuse
```

Session Context 不等于 Personal Knowledge，也不自动写入长期知识库。

### Phase 16 — Retrieval Intelligence & Knowledge Graph

优先 hierarchical retrieval：

```text
Domain → Repository/Project → Document → Section → Chunk
```

Graph Retrieval 作为新增 path，与 Vector / Hierarchical 并存，关系必须有 provenance。

### Phase 17 — Agent Analyst

```text
Question + Session Context + Knowledge Catalog + Capabilities
→ AgentAnalyst
→ structured AnalysisDecision
→ Policy Validator
→ Executor
```

简单请求继续走 deterministic fast path。

### Phase 18 — Advanced Agent Autonomy / Bounded ReAct

仅在前述阶段稳定后允许 bounded replan；第一版 `max_replans=1` 起步，不允许无界 ReAct。

### Phase 19 — Owner Agent / Advanced Permissions

Owner-only private/write/destructive capability 必须单独设计 server-side policy、step-up authentication、audit 与 human confirmation。

## 11. Package Boundaries

当前主要模块：

```text
src/zglab_rag/
├── agent/
├── api/
├── application/
├── auth/
├── capabilities/
├── domain/
├── embeddings/
├── evaluation/
├── generation/
├── indexing/
├── ingestion/
├── mcp/
├── research/
├── retrieval/
├── reranking/
├── sources/
└── storage/
```

未来 `session/`、hierarchical / graph retrieval、analyst 等只在对应 Phase 获得授权后渐进加入，不提前创建无用空壳。

## 12. Evaluation

Evaluation 是跨阶段基础设施：

```text
Embedding → Retrieval → Generation
→ Auth/Security → Research → MCP → Agent → Session → Advanced Retrieval
```

任何新的 retrieval / planner / autonomy 策略都必须可评测和回归，不凭主观体验替换生产默认。
