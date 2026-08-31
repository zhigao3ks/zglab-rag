# 开发计划

本文件描述当前开发阶段与执行顺序。Phase 11+ 的最终编号和优先级以 `docs/roadmap-v2.md` 为准。

## 1. 已完成基础

### Phase 0–10 — Personal Knowledge Assistant Foundation ✅ SEALED

已完成：

- Source Registry / public-private boundary；
- Markdown / Local Git ingestion；
- structure-aware chunking 与稳定 ID；
- Embedding benchmark；
- SQLite + sqlite-vec persistent index；
- Vector / Lexical / Hybrid / Reranker evaluation；
- Evidence-Grounded Generation；
- Citation Validation；
- API / SSE / Vue Web UI；
- Nginx / HTTPS / systemd / sync / backup；
- `https://ask.zglab.fun` 生产部署与验收。

历史细节由 Phase 0–10 对应文档与 acceptance 保留。

## 2. 已封板 Agent Foundation

### Phase 11 — Authentication & Access Control ✅ SEALED

完成统一 Security Foundation：

- no public signup；
- Admin CLI provisioning + single-use activation；
- Argon2id；
- server-side opaque session；
- Secure HttpOnly Cookie；
- CSRF / Origin validation；
- login throttling；
- per-user quota / concurrency；
- independent `auth.db`；
- kill switch / audit / v1 retirement。

### Phase 12 — Capability Foundation & Web Research ✅ SEALED

完成：

```text
PersonalKnowledgeSkill
+
WebResearchSkill
```

Web Research 使用 bounded Search → safe fetch → extraction → ExternalEvidence → Grounded Generation → Citation Validation。
Web evidence 是 request-scoped、untrusted data，不写入 Personal Knowledge。

### Phase 13 — MCP Tool Runtime ✅ SEALED

完成：

```text
zglab-tools Shared Tool Core
→ TypeScript MCP Server
→ stdio
→ Python MCP Client
→ zglab-rag MCPToolRuntime
```

生产 MCP runtime 为独立 Node 22 internal runtime，不开放公网 MCP endpoint。

### Phase 14 — Agent Orchestrator ✅ COMPLETE / PRODUCTION ACCEPTED / SEALED

完成：

```text
AgentRequest
→ BoundedPlanner
→ Validated AgentPlan
→ BoundedAgentExecutor
→ AgentObservation[]
→ Final Synthesis
→ AgentAnswer
```

冻结边界：

```text
max steps = 4
Personal <= 1
Web <= 1
MCP <= 3
deterministic planner
sequential executor
no automatic retry
no replanning
no infinite ReAct
```

继续保持：

- Evidence before Persona；
- ToolResult != Evidence；
- Web Evidence 是 untrusted data；
- MCP Host allowlist 是授权边界；
- Planner proposes, Executor enforces；
- Auth / quota / concurrency / kill switch 在 Agent Runtime 外部。

生产验收：`docs/evaluations/phase-14-production-acceptance-2026-08-28.md`。

---

## 3. 当前立即任务 — UX Track

状态：**IMMEDIATE**。

Phase 15 暂不开始，先稳定现有 Vue 产品体验。

### UX-1 — Viewport / Layout Foundation

目标：

- Assistant 变为 viewport-bounded application shell；
- message area 成为独立滚动容器；
- Composer 位于滚动容器外部并稳定停留在底部；
- Header / account area / workspace 宽度统一；
- 不再由 document body 承担聊天历史滚动。

### UX-2 — Smart Follow

目标：

- 发送新问题时定位最新消息；
- 用户位于底部附近时，SSE 更新自动跟随；
- 用户主动向上查看历史时停止强制跟随；
- 显示“回到最新消息”；
- 点击或手动回到底部后恢复 follow mode；
- completed / error 使用相同 terminal scroll policy。

建议采用两态 UI 状态机：

```text
FOLLOWING
↕
DETACHED
```

### UX-3 — Responsive Hardening

重点：

- Header / Navigation viewport 行为；
- mobile / tablet / desktop gutters；
- Composer controls wrapping；
- textarea 最大高度；
- long answer / source layout；
- dynamic viewport height。

### UX-4 — Regression / Acceptance

至少验证：

```text
Auto
Personal
Web
Agent

short conversation
long conversation
SSE pending/completed/error
logout/change-password
mobile/tablet/desktop viewport
```

测试与 build：

```bash
cd web
npm run test:run
npm run build
```

### UX Track 明确不做

- 不新增 conversation_id；
- 不持久化消息；
- 不把历史消息发送给后端；
- 不新增 Session Sidebar 数据能力；
- 不改变 Agent / RAG / Auth / Web / MCP 语义；
- 不实现 retry / replanning / ReAct。

可以为未来 Sidebar 预留 layout slot，但不得提前实现 Phase 15 数据模型。

---

## 4. Phase 15 — Conversation & Session Memory

UX Track 完成后才进入。

### 15A — Conversation Persistence

- Conversation / Message lifecycle；
- 新建会话；
- 会话列表；
- 切换 / 恢复；
- 删除 / 归档策略。

### 15B — Multi-turn Context

- 指代解析；
- bounded history assembly；
- recent turns + relevant turns；
- 不无限拼接聊天历史。

### 15C — Context Compression

- conversation summary；
- token / byte budget；
- deterministic truncation；
- summary 属于 session state，不等于 Personal Knowledge。

### 15D — Session Resource Reuse

受控复用：

- temporary web evidence；
- previous personal retrieval；
- tool artifacts；
- derived summaries。

必须具备 TTL / max items / max bytes / provenance / invalidation。

---

## 5. Phase 16 — Retrieval Intelligence & Knowledge Graph

优先 hierarchical / structure-aware retrieval，再评估 Graph Retrieval。

```text
Knowledge Domain
→ Repository / Project
→ Document Summary
→ Section
→ Chunk
```

Graph 作为新的 retrieval path，不替换 Vector：

```text
Vector
Hierarchical
Graph
Hybrid combinations
```

所有关系必须有 provenance，模型生成关系不能自动成为可信事实。

---

## 6. Phase 17 — Agent Analyst

目标：建立 deterministic fast path + LLM Analyst 的混合分析层。

复杂请求可生成结构化 `AnalysisDecision`：

- intent；
- rewritten query / subqueries；
- knowledge scope；
- retrieval strategy；
- capability selection；
- bounded plan。

安全原则：

```text
Analyst proposes
Policy Validator validates
Executor enforces
```

简单工具任务仍走 deterministic fast path，避免所有请求都增加一次 planner LLM 调用。

---

## 7. Phase 18 — Advanced Agent Autonomy / Bounded ReAct

仅在 Session、Retrieval Intelligence、Analyst 稳定后进入。

第一版：

- `max_replans = 1` 起步；
- max steps / research / tool calls；
- overall deadline；
- no recursive / unbounded ReAct；
- no permission escalation。

禁止直接实现无界：

```python
while not done:
    think()
    act()
```

---

## 8. Phase 19 — Owner Agent / Advanced Permissions

普通公网用户继续只允许低风险 capability。

Owner Agent 才考虑：

- private knowledge；
- authenticated private sources；
- GitHub / filesystem write；
- deploy / admin operation；
- human confirmation；
- step-up authentication；
- destructive-operation policy；
- richer long-term memory（若届时有明确价值）。

---

## 9. Cross-phase Quality Track

以下工作不单独占 Product Phase：

- evaluation；
- performance；
- latency；
- monitoring；
- cache；
- answerability；
- security regression；
- documentation reconciliation；
- deployment / rollback hardening。

任何算法或架构替换都应有可比较评测，不凭主观体验宣布改进。
