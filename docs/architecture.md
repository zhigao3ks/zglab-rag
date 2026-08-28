# ZGLab Personal AI Agent — 架构设计

> **Roadmap authority**：Phase 11+ 的编号与执行顺序以 `docs/roadmap-v2.md` 为准。
> Phase 0–10 是已经完成并投入生产的 Personal Knowledge Assistant Foundation。
> 本文保留其核心架构，并描述后续向 Personal AI Agent 演进的边界。

## 1. 系统定位

ZGLab 当前已经具备一个生产可用的 Personal Knowledge Assistant：

```text
Question
  ↓
Public Knowledge Retrieval
  ↓
Evidence Context
  ↓
Grounded Generation
  ↓
Citation Validation
  ↓
Answer + Sources
```

长期产品目标已经扩展为：

> **以个人身份为主体的 ZGLab Personal AI Agent。**

未来系统不是简单把 RAG、Web Search 和 MCP 串在一起，而是把三类能力放在统一 Agent Control Plane 下：

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

Persona 只影响表达方式，不是事实来源。

## 2. 已完成的 RAG Foundation（Phase 0–10）

现有系统已经完成：

- Source Registry；
- Local / Local Git ingestion；
- Markdown structure-aware chunking；
- deterministic document/chunk ID；
- Embedding evaluation；
- SQLite + sqlite-vec persistent index；
- FTS5 lexical retrieval；
- Hybrid / RRF evaluation；
- CrossEncoder Reranker evaluation；
- Evidence-Grounded Generation；
- Citation Validation；
- Public API v1；
- SSE status streaming；
- Vue Web UI；
- Production sync / backup / deployment；
- `ask.zglab.fun` 公网验收。

这些能力不会因为 Agent 化而被重写。

## 3. Knowledge Model

知识库逻辑上继续划分：

### Identity

经过审核的稳定个人事实，例如：Profile、教育、技术方向、公开联系方式等。

### Projects

项目 README、架构文档、设计决策、问题复盘与公开项目 Notes。

### Knowledge

来自 Notes 的可复用技术知识、工程经验和方法论。

### Experience

允许公开分享的经历、论文、竞赛、研究或学习信息。

### Dynamic Sources

需要同步的 Git 仓库与近期项目文档。动态来源不得静默覆盖更高置信度的稳定事实。

完整知识模型见 `docs/knowledge-model.md`。

## 4. Public / Private Boundary

当前生产知识检索始终强制：

```text
visibility = public
```

不得索引或公开：

- 公司内部仓库；
- 客户数据；
- 合同原文；
- 私人消息；
- Secret / Credential；
- 未授权 private repository；
- 未明确允许公开的个人敏感信息。

**Phase 11 增加登录不等于 private knowledge 自动开放。**

Private / owner-only knowledge 必须在未来 Owner Agent / Advanced Permissions 阶段单独设计鉴权、审计与授权策略。

## 5. Ingestion / Index Architecture

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
- embedding profile 明确版本化；
- new / changed / unchanged / deleted 增量规划；
- embedding 在事务外完成；
- metadata / vector / FTS / snapshot 原子更新；
- source sync failure 不破坏上一版可服务 index。

## 6. Retrieval Architecture

生产默认路径：

```text
Question
   ↓
Query Embedding
   ↓
sqlite-vec Vector Search
   ↓
public/source/scope relational filtering
   ↓
Top K Evidence
```

现有替代路径：

```text
Vector
Lexical / FTS5
Hybrid / RRF
Vector Top-N → CrossEncoder Reranker
```

所有 Retriever 都必须在 metadata 暴露之前执行 public/source/scope 过滤。

当前评测结论：

- Vector 仍为生产默认；
- Hybrid baseline 未超过 Vector；
- Reranker 明显提升质量，但 CPU 延迟与 RSS 对当前 2C2G 生产预算偏高，因此保持显式可选。

## 7. Grounded Generation Architecture

Phase 8 已冻结：

```text
Question
  ↓
RetrievalResult[]
  ↓
ContextBuilder
  ↓
Evidence E1...En
  ↓
GenerationProvider
  ↓
claim-level structured output
  ↓
CitationValidator
  ↓
GroundedAnswer + Sources
```

核心边界：

- Persona 不是 Evidence；
- Evidence 作为只读、不可信数据进入模型 Context；
- LLM 只能引用本次分配的短 Evidence ID；
- Citation validity / ownership / coverage 由确定性代码检查；
- 检索为空、模型判定不足或校验无法安全恢复时返回 insufficient evidence；
- 不依赖 Prompt 自觉保证真实性。

完整设计见 `docs/generation-grounding.md`。

## 8. Production Architecture

Phase 10 已完成：

```text
Internet
   ↓ HTTPS
ask.zglab.fun
   ↓
Nginx
   ├── Vue SPA
   └── FastAPI / SSE
          ↓
      SQLite + sqlite-vec
      local BGE
      external LLM API
```

生产服务包含：

- `zglab-rag-api.service`；
- `zglab-rag-backup.service` + timer；
- `zglab-rag-sync.service` + timer；
- Nginx / HTTPS；
- `/health` / `/ready`；
- atomic SQLite backup；
- fast-forward-only registered Git source sync。

详细说明见 `docs/production-architecture.md`。

---

# Agent Roadmap Architecture

## 9. Phase 11 — Authentication & Access Control（已实现）

Phase 11 已实现并验收（见 `docs/evaluations/phase-11-authentication-acceptance.md`）。
设计与契约冻结在 `docs/authentication.md` 与 `docs/api-v2.md`。

在所有新的 Web Search、MCP、Agent 多步调用能力之前，先建立的统一 Security Foundation：

```text
Internet
   ↓
Nginx
   ↓
Public Landing                 # anonymous, no external cost
   │
   └── Login
         ↓
Authentication
         ↓
Authorization
         ↓
Quota / Rate Limit
         ↓
Application / Future Agent Runtime
```

### Auth Data Boundary

实现：

```text
runtime/
├── knowledge.db     # knowledge/index lifecycle
└── auth.db          # identity/session/security lifecycle（独立 schema version，WAL，fail-fast）
```

两种数据库不得混为一个 lifecycle；认证模块位于 `src/zglab_rag/auth/`，
不触碰检索/生成内部，API 层通过 `api/security.py` 的 `AuthRuntime` 接入。

### Session Model

Phase 11 优先：

```text
Server-side Session
+ Secure HttpOnly Cookie
+ CSRF / Origin validation
+ revocation
+ idle / absolute expiration
```

不默认采用 JWT + localStorage。

### Account Provisioning

```text
Admin CLI
   ↓
Create User
   ↓
Single-use Activation Token
   ↓
User sets own password
   ↓
Active Account
```

禁止 public signup。

### Cost Boundary

Authenticated user 仍必须经过：

- per-user rate limit；
- daily quota；
- concurrency guard；
- timeout；
- request size；
- capability kill switch。

Authentication 不能替代资源保护。

实现上的完整安全顺序（`/api/v2/ask` 与 `/api/v2/ask/stream` 共用，SSE 无旁路）：

```text
Request Validation → Kill Switch → Origin → Authentication → Authorization
→ CSRF → Quota → Question length → Concurrency → GroundedAnswerService
```

旧 `/api/v1` 由 `ZGLAB_RAG_API_V1_RETIRED` 控制退役（410 API_RETIRED），
Phase 9 历史契约保留在 `docs/public-api.md`。

## 10. Phase 12 — Capability Foundation & Web Research

原 2026-08-21 冻结的 Web Research 设计已经顺延到 Phase 12，详见 `docs/web-research-skill.md`。

**Phase 12A（已实现）**：现有 RAG 已封装为 `PersonalKnowledgeSkill`，API v2
经 Capability boundary（`src/zglab_rag/capabilities/`）调用；设计见
`docs/capability-architecture.md`。

**Phase 12B（已实现）**：`src/zglab_rag/research/` 提供独立 bounded 的
Web Research 管线（Search → Safe Fetch → Extract → ExternalEvidence）；
设计见 `docs/web-research-runtime.md`。

**Phase 12C（已实现）**：`ExternalEvidence[]` 经 `research/web_adapter.py`
（W→E 确定性映射、origin=web、不伪造 chunk 身份）进入从 Phase 8 抽出的共享
`generate_from_context()`，复用同一套 Citation Validation / repair / claims
渲染；citation URL 只能来自 provenance；仅内部能力，未接入公网 API；设计见
`docs/web-evidence-grounding.md`。

**Phase 12D（已生产验收/封板）**：确定性非 LLM capability selection
（auto/personal/web）把两个 Skill 接入 `/api/v2/ask(/stream)`；additive
`mode` 与 web source（origin/url/domain）；SSE `researching`；独立 web
quota / permission / 并发；DNS rebinding 以 pinned resolution 关闭；
前端 mode 控件与安全外链。2026-08-28 已完成真实 provider、HTTPS API/SSE、
安全开关、quota/concurrency、故障隔离、auth v3 与备份验证；封板证据见
`docs/evaluations/phase-12-production-acceptance-2026-08-28.md`，设计见
`docs/web-research-product.md`。

Phase 12 首先把现有 RAG 变成一个稳定能力：

```text
PersonalKnowledgeSkill
```

然后增加：

```text
WebResearchSkill
```

两者都应拥有清晰 input/output contract，而不是让未来 Agent 直接依赖底层 SQLite / Search vendor。

### Research Pipeline

```text
Question
   ↓
Research Eligibility
   ↓
SearchProvider
   ↓
Candidate Selection
   ↓
Safe Fetcher
   ↓
Extraction / Normalization
   ↓
External Evidence
   ↓
Grounded Generation
   ↓
Citation Validation
```

Web Evidence 在 Phase 12 只允许 request-scoped，不进入长期 Personal Knowledge。

### External Evidence Security

必须同时处理：

- Prompt Injection as untrusted data；
- SSRF；
- private / loopback / metadata address blocking；
- redirect re-validation；
- response-size / content-type / timeout limits；
- URL provenance；
- hallucinated citation rejection。

## 11. Phase 13 — MCP Tool Runtime

MCP 用于确定性执行能力，不用于替代 Evidence Research。

```text
zglab-tools
   │
   ├── Browser UI
   │
   └── Shared Tool Core
          ↑
       MCP Server
```

优先暴露：JSON、timestamp、Base64、URL、UUID、text processing、hash、DOI 等无副作用工具。

生产第一版 MCP Server 优先 localhost/internal，不直接暴露公网。

## 12. Phase 14 — Agent Control Plane

只有当 PersonalKnowledgeSkill、WebResearchSkill 和 MCP Runtime 都稳定后，才引入：

```text
Agent Runtime
├── Capability Registry
├── Router / Planner
├── Policy Engine
├── Bounded Executor
├── Observation Model
└── Final Synthesis / Validation
```

第一版使用 Bounded Planner + Executor：

- max steps；
- max research；
- max MCP calls；
- deadline；
- capability allowlist；
- cost / permission boundary。

不一开始实现无限 ReAct loop。

## 13. Observation Model

未来 Agent 不应把所有东西都叫 Evidence。

建议：

```text
AgentObservation
├── Evidence
│   ├── PersonalEvidence
│   └── WebEvidence
│
└── ToolObservation
    └── MCPToolResult
```

事实性知识需要 Citation；确定性 Tool Result 则根据 tool contract 验证，不强行伪装成网页 Evidence。

## 14. Phase 15 — Session Context

必须区分：

```text
Personal Knowledge      = reviewed, long-lived
External Evidence       = temporary
Session Context         = temporary conversation state
Long-term Agent Memory  = separate future concern
```

Phase 15 才实现：

- conversation reference；
- temporary Web Evidence reuse；
- Tool Artifact reuse；
- bounded in-memory / ephemeral store；
- TTL / max sessions / max bytes。

单实例初版不因 Session 自动引入 Redis。

## 15. Phase 16 — Owner Agent / Advanced Permissions

普通 authenticated user 默认只能使用安全、低副作用 capability。

Owner-only 能力未来单独设计：

- private knowledge；
- GitHub write；
- files；
- deploy；
- admin operations；
- destructive actions。

必须使用强制 server-side Policy、step-up authentication 与 human confirmation。

## 16. Package Evolution

Phase 0–10 已有模块继续保留：

```text
src/zglab_rag/
├── api/
├── application/
├── domain/
├── embeddings/
├── evaluation/
├── generation/
├── indexing/
├── ingestion/
├── retrieval/
├── reranking/
├── sources/
└── storage/
```

已增加与未来渐进增加（不一次创建空壳）：

```text
auth/           # Phase 11（已存在）
capabilities/   # Phase 12A（已存在：PersonalKnowledgeSkill）
research/       # Phase 12B/12C/12D（已存在：Web Research Core + Evidence Grounding + 产品接入）
mcp/            # Phase 13
agent/          # Phase 14
session/        # Phase 15
```

Domain contract 不得耦合 FastAPI、具体模型厂商或 Agent Framework。

## 17. Evaluation

Evaluation 仍是跨阶段基础设施：

```text
Embedding → Retrieval → Hybrid → Reranker → Generation
→ Auth/Security → Research → MCP → Agent
```

每种新 capability 必须可单独评测和回归，不允许只凭“对话效果看起来不错”宣布完成。

## 18. Post-v1 Optimization

以下能力继续作为非编号优化轨道：

- Reranker quantization；
- latency；
- cache；
- monitoring；
- evaluation expansion；
- answerability；
- advanced Hybrid tuning。

这些优化不覆盖或改变 `docs/roadmap-v2.md` 中 Phase 11–16 的 Product Capability 编号。
