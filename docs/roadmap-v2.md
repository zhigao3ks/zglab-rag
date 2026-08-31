# ZGLab Personal AI Agent — Roadmap v2

> **Roadmap authority**：本文档自 **2026-08-25** 起是 Phase 11 及以后阶段的权威路线图。
> Phase 0–10 的历史编号、实现内容与验收记录保持不变；Phase 11–14 已完成并封板。
> **2026-08-28 Phase 14 封板后，后续优先级按“先体验、再记忆、再检索智能、再分析、最后提高自治”重新排序。**
> 如果旧文档、历史验收记录或注释中的 future phase 与本文档冲突，以本文档为准。

## 1. 项目定位

Phase 0–10 已完成 Personal Knowledge Assistant 基础设施并投入生产：

- Markdown / Git knowledge ingestion；
- structure-aware chunking 与稳定 document/chunk ID；
- BGE Embedding benchmark；
- SQLite + sqlite-vec persistent index；
- Vector / Lexical / Hybrid / Reranker evaluation；
- Evidence-Grounded Generation + Citation Validation；
- API / SSE / Vue Web UI；
- Nginx / HTTPS / systemd / backup / sync；
- `https://ask.zglab.fun` 公网部署与验收。

随后系统演进为：

> **以个人知识为核心，结合 Web Research、MCP Tool Runtime 与 Bounded Agent Orchestrator 的 ZGLab Personal AI Agent。**

当前三类正式 capability：

1. **Personal Knowledge Skill**：Evidence-Grounded RAG；
2. **Web Research Skill**：受控公网搜索、抓取、抽取与可验证 External Evidence；
3. **MCP Tool Runtime**：受 allowlist、无副作用、确定性的工具调用。

统一 Agent Runtime 已在 Phase 14 完成生产封板。

---

## 2. 当前权威 Roadmap

```text
Phase 0–10  Personal Knowledge Assistant Foundation        ✅ SEALED
Phase 11    Authentication & Access Control                ✅ SEALED
Phase 12    Capability Foundation & Web Research           ✅ SEALED
Phase 13    MCP Tool Runtime                               ✅ SEALED
Phase 14    Agent Orchestrator                              ✅ SEALED

UX Track     Frontend / Product Experience Stabilization   ✅ COMPLETE

Phase 15    Conversation & Session Memory                   ← IN PROGRESS (15A ✅, 15B NEXT)
Phase 16    Retrieval Intelligence & Knowledge Graph
Phase 17    Agent Analyst
Phase 18    Advanced Agent Autonomy / Bounded ReAct
Phase 19    Owner Agent / Advanced Permissions
```

当前开发优先级：

```text
先让它好用
    ↓
再让它记得住
    ↓
再让它更会找
    ↓
再让它更会分析
    ↓
最后才让它更自主
```

这取代“单纯按照 Agent 技术复杂度继续向前堆能力”的旧排序。

Evaluation、performance、monitoring、documentation reconciliation 等继续作为跨阶段基础设施，不单独占 Product Phase。

---

## 3. 已封板阶段

### Phase 11 — Authentication & Access Control ✅

建立统一 Security Foundation：

- No public signup；
- Admin CLI provisioning + single-use activation；
- Argon2id；
- server-side opaque session；
- Secure HttpOnly cookie；
- CSRF / Origin validation；
- per-IP / per-identity throttling；
- per-user quota / concurrency；
- independent `auth.db`；
- kill switch / audit / v1 retirement。

认证、授权、quota 必须位于 Agent Runtime 之外，Agent 不得绕过服务端安全边界。

### Phase 12 — Capability Foundation & Web Research ✅

完成：

```text
PersonalKnowledgeSkill
+
WebResearchSkill
```

Web 路径：

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

Web evidence 始终是 request-scoped、untrusted，不写入长期 Personal Knowledge；URL 必须来自真实 provenance。

### Phase 13 — MCP Tool Runtime ✅

跨仓库正式边界：

```text
zglab-tools Shared Tool Core
        ↓
Official TypeScript MCP Server
        ↓ stdio
Official Python MCP Client
        ↓
zglab-rag MCPToolRuntime
```

第一批 10 个 deterministic / side-effect-free tool：

- JSON format / minify / validate；
- Base64 encode / decode；
- URL encode / decode；
- text count / deduplicate；
- timestamp convert。

生产使用独立 Node 22 runtime；MCP 仅 stdio/internal，不开放公网 endpoint。

### Phase 14 — Agent Orchestrator ✅

已完成并生产封板：

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

当前 Planner 以 deterministic fast path 为主，支持：

- Personal；
- Web；
- Tool；
- Personal → Web 组合。

冻结执行边界：

```text
max steps = 4
Personal <= 1
Web <= 1
MCP <= 3
overall deadline
no retry / no replan / no infinite ReAct
```

`ToolResult != Evidence`，Web content 不能修改冻结计划。

---

## 4. UX Track — Frontend / Product Experience Stabilization ✅ COMPLETE

这是 Phase 14 后**最高优先级工作**，作为非编号 Maintenance / Product UX Track 执行，不改变已封板 Phase 编号。

当前主要体验问题：

- Composer / 输入框随着会话增长向下漂移；
- 新回答完成后不会自动定位到最新消息；
- SSE 过程中缺乏合理的智能跟随；
- 用户主动上翻时缺少“回到最新消息”机制；
- Header / Navigation 宽度与 viewport 行为不稳定；
- 长会话整体布局不适合持续使用；
- 后续 Multi-session Sidebar 若加入，当前页面结构可能需要重构。

目标布局：

```text
┌────────────┬───────────────────────────────┐
│            │ Header / Navigation           │
│ Sessions   ├───────────────────────────────┤
│ (future)   │                               │
│            │ Message Scroll Area           │
│            │                               │
│            ├───────────────────────────────┤
│            │ Sticky Composer               │
└────────────┴───────────────────────────────┘
```

重点行为：

- sticky composer；
- independent message scroll container；
- send / new response auto-scroll；
- SSE 仅在用户位于底部附近时自动跟随；
- 用户主动上翻时停止强制 scroll，并显示“回到最新消息”；
- fixed/responsive header width；
- desktop/mobile viewport 适配；
- 不破坏现有 Auto / Personal / Web / Agent 功能。

该 Track 已完成：Assistant 现为 viewport-bounded shell，消息区域独立滚动，Composer 固定在 dock，且 FOLLOWING / DETACHED 状态机不会在用户上翻时抢占滚动位置。没有实现任何 Phase 15 session 或持久化能力；下一 Product Phase 仍为 Phase 15。

---

## 5. Phase 15 — Conversation & Session Memory

目标：把当前 request-oriented Agent 升级为真正的 multi-turn Conversation Agent。

### 15A — Conversation Persistence ✅ COMPLETE

#### 15A1 — Conversation Domain & Persistence Foundation ✅ COMPLETE

已完成独立 `conversation.db` 的 schema v1、framework-free Conversation / Message domain，以及 owner-scoped repository。该基础层尚未接入 API、SSE、前端、ask request 或 multi-turn context；15A 与整个 Phase 15 仍未完成。

#### 15A2 — Authenticated Conversation API + Ask Persistence ✅ COMPLETE

已完成 owner-scoped authenticated Conversation API，以及 `/api/v2/ask` / `/api/v2/ask/stream` 的可选 `conversation_id` 持久化。历史消息仍不进入 retrieval、prompt、capability 或 Agent context。

#### 15A3 — Session Sidebar + History Restore ✅ COMPLETE

已完成 Vue 前端 Session Sidebar 与历史恢复（Phase 15A 收尾）：

- Assistant Layout 正式加入 `Session Sidebar` sibling：会话列表（按后端 `updated_at DESC` 顺序展示）、新建会话、active 状态、删除入口；
- 新建会话使用固定默认标题「新对话」，不使用 LLM 生成标题；创建后立即激活并清空本地消息，后续 ask 携带该 `conversation_id`；
- 切换会话通过 owner-scoped `GET /api/v2/conversations/{id}/messages` 恢复历史，USER/ASSISTANT 仅映射为本地 `ChatMessage[]` 展示；**历史消息从不重新发送给 ask API，也从不进入 retrieval、prompt、capability 或 Agent context**；
- 删除会话带两步确认；删除 active 会话后回到安全 empty state；
- 服务端 404（`NOT_FOUND`，含另一 session 删除的场景）统一回退到安全提示 + 刷新列表 + 清空 active conversation，不展示原始 server message；
- 无 active conversation 时 ask 不携带 `conversation_id`，也不自动创建会话；
- desktop 为固定宽度侧栏；mobile 为可展开/收起的 drawer + backdrop，不改变既有 viewport shell 与 FOLLOWING/DETACHED 状态机。

至此 15A Conversation Persistence 整体完成。在 15B Multi-turn Context 落地之前，历史消息仍然只是 persisted UI history。

建立明确的 Conversation / Message lifecycle：

```text
Conversation
├── conversation_id
├── title
├── created_at
└── updated_at

Message
├── message_id
├── conversation_id
├── role
├── content
└── created_at
```

产品支持：

- 新建会话；
- 会话列表；
- 切换会话；
- 历史会话恢复；
- 删除/归档策略。

### 15B — Multi-turn Context

解决指代与连续理解：

```text
Q1: 我的 RAG 项目用了什么 embedding？
A1: BGE-small-zh-v1.5 ...

Q2: 那它跟 E5 相比呢？
```

Context assembly 需要 bounded，不允许简单无限拼接历史消息。

### 15C — Context Compression

长会话需要：

- recent turns；
- conversation summary；
- relevant historical turns；
- token / byte budget；
- deterministic truncation policy。

Summary 是 conversation state，不等于 Personal Knowledge。

### 15D — Session Resource Reuse

Session 不只是聊天记录，也是短期工作空间：

```text
Session Workspace
├── Messages
├── Conversation Summary
├── Retrieved Personal Evidence
├── Temporary Web Evidence
├── Tool Results
└── Derived Artifacts
```

允许受控复用：

- temporary web evidence；
- previous personal retrieval results；
- tool artifacts；
- derived summaries。

必须有 TTL / max items / max bytes / provenance / invalidation。

### 明确边界

```text
Personal Knowledge      = reviewed, long-lived
Session Context         = conversation-scoped state
Temporary Evidence      = request/session-scoped evidence
Long-term Agent Memory  = separate future concern
```

Phase 15 不自动把聊天内容写入 Personal Knowledge，也不建立无边界长期用户画像。

---

## 6. Phase 16 — Retrieval Intelligence & Knowledge Graph

目标：把当前“全库 Flat Chunk Top-K”逐步升级为“理解知识结构后再检索”。

### 16A — Hierarchical / Structure-aware Retrieval

优先利用当前 Markdown / Git 已存在的结构：

```text
Knowledge Domain
    ↓
Repository / Project
    ↓
Document Summary
    ↓
Section
    ↓
Chunk
```

建立 document-level metadata，例如：

- document title；
- repository / project；
- document summary；
- section outline；
- keywords / topics；
- provenance。

目标流程：

```text
Question
→ Candidate Documents
→ Candidate Sections
→ Detailed Chunks
→ Evidence
```

Hierarchical Retrieval 优先于直接引入复杂 GraphRAG。

### 16B — Knowledge Graph / Graph Retrieval

Knowledge Graph 作为新的 retrieval path，而不是替换 Vector：

```text
Retrieval Layer
├── Vector Retrieval
├── Hierarchical Retrieval
└── Graph Retrieval
```

重点解决：

- entity relationship；
- project ↔ technology；
- person ↔ project ↔ experience；
- multi-hop question；
- cross-document aggregation。

示例：

```text
Person
 ├── developed → Project
 ├── worked_on → Experience
 └── uses → Technology

Project
 ├── uses → MCP
 ├── uses → RAG
 └── related_to → Knowledge Note
```

Graph 构建必须有 provenance，不能让模型生成的关系自动成为可信事实。

### 16C — Retrieval Evaluation

对比：

- Vector；
- Hierarchical；
- Graph；
- Hybrid combinations。

根据真实 benchmark 决定生产默认，而不是为了使用 GraphRAG 而强行替换现有 Vector path。

---

## 7. Phase 17 — Agent Analyst

目标：把当前 deterministic Planner 升级为“Fast Path + LLM Analyst”的混合分析层。

不单独堆叠多个 LLM：

```text
Planner LLM
→ Query Rewrite LLM
→ Retrieval
```

而是把复杂请求的理解合并为统一 Analysis Stage：

```text
Question
+
Session Context
+
Knowledge Catalog
+
Available Capabilities / Tools
        ↓
AgentAnalyst
        ↓
Structured AnalysisDecision
```

Analyst 可输出：

- intent；
- rewritten query / subqueries；
- knowledge scope；
- relevant project / document / section hints；
- retrieval strategy；
- capability selection；
- bounded execution plan。

示例：

```text
Question:
“我的 RAG 项目与当前主流 Agentic RAG 相比还有哪些不足？”

AnalysisDecision:
- intent: personal_project_comparison
- personal scope: zglab-rag / architecture / agent docs
- personal query: zglab-rag retrieval and agent architecture
- web query: current agentic RAG architecture
- plan: Personal → Web → Synthesis
```

简单请求继续走 deterministic fast path：

```text
“格式化这个 JSON”
→ json_format
```

不要为了 Agent 化让所有请求都多一次 LLM planner 调用。

### 安全原则

```text
Analyst proposes
Policy Validator validates
Executor enforces
```

LLM Analyst 永远不直接拥有无限执行权。

---

## 8. Phase 18 — Advanced Agent Autonomy / Bounded ReAct

只有 Session、Retrieval Intelligence 与 Analyst 都稳定后，才提高自治能力。

目标从：

```text
Analyze
→ Plan
→ Execute
→ Answer
```

升级为：

```text
Analyze
→ Plan
→ Execute
→ Observe
→ Evaluate
       ↓
   need replan?
   ├── no  → Answer
   └── yes → bounded Replan
```

第一版必须保持 bounded：

- `max_replans = 1` 起步；
- max steps；
- max research；
- max tool calls；
- overall deadline；
- no recursive/unbounded ReAct；
- no autonomous permission escalation。

后续再评估：

- plan repair；
- limited retry；
- alternative retrieval；
- observation evaluator；
- bounded ReAct-like loop。

不要直接实现：

```python
while not done:
    think()
    act()
```

---

## 9. Phase 19 — Owner Agent / Advanced Permissions

公网普通用户继续只允许低风险 capability。

Owner Agent 才考虑：

- owner-only private knowledge；
- authenticated private sources；
- GitHub write；
- filesystem write；
- deploy / admin operations；
- human confirmation；
- step-up authentication；
- destructive-operation policy；
- richer long-term memory / owner profile（若届时仍有明确价值）。

MCP annotations 只能作为 hint，真正权限仍由 Agent Host / Policy Engine 强制执行。

---

## 10. Cross-phase Quality / Maintenance Track

以下问题不需要等到某个 Product Phase 才能修：

- frontend UX / accessibility / responsive layout；
- production observability / metrics；
- latency / RSS / cost profiling；
- Reranker quantization / cache；
- evaluation expansion；
- test flakiness；
- SQLite / filesystem environment issues；
- Node/Python runtime maintenance；
- documentation reconciliation；
- security fixes；
- deployment / rollback hardening。

原则：

> **用户体验或生产稳定性问题优先于继续增加新的 Agent capability。**

---

## 11. 文档与历史记录

当前设计文档应与本 Roadmap 保持一致：

- `README.md`
- `AGENTS.md`
- `docs/development-plan.md`
- `docs/architecture.md`
- `docs/production-architecture.md`
- `docs/agent-architecture.md`
- `docs/agent-product.md`
- Web / MCP / Auth 对应设计文档。

历史验收文档记录当时真实发生的状态，不因为未来 Roadmap 调整而重写历史。

例如：

```text
docs/evaluations/phase-11-*.md
docs/evaluations/phase-12-*.md
docs/evaluations/phase-13-*.md
docs/evaluations/phase-14-*.md
```

旧文档中的 future phase 编号只表示当时规划，不再具有当前 Roadmap 权威性。

---

## 12. Codex / Coding Agent 执行规则

开始新任务前必须：

1. 阅读 `AGENTS.md`；
2. 阅读本文档；
3. 将本文档视为当前 Roadmap authority；
4. 不根据历史文档中的旧 future-phase 编号提前实现功能；
5. 每次只实现当前明确授权的 Phase / Track / vertical slice；
6. 如发现文档与生产现实冲突，先报告并修正文档漂移；
7. 不因为某项技术“更 Agentic”而跳过更高优先级的产品体验和稳定性问题。

UX Track 已完成并验收。下一 Product Phase：

> **Phase 15 — Conversation & Session Memory**
