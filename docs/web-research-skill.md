# Web Research Skill 设计（Phase 12）

> **Roadmap Supersession Note — 2026-08-25**
>
> 本设计最初于 2026-08-21 以 **Phase 11 — External Research & Session Evidence** 冻结。
> Phase 10 完成生产部署后，项目长期目标扩展为 ZGLab Personal AI Agent，并决定在所有新的
> cost-bearing capability 之前先完成 Authentication & Access Control。
>
> 因此：
>
> - Web Research 技术设计本身继续有效；
> - 当前编号顺延为 **Phase 12 — Agent Capability Foundation & Web Research**；
> - 原设计中的完整 Session Evidence Reuse 不再属于本阶段，统一移动到 **Phase 15 — Session Context**；
> - Phase 12 第一版 External Evidence 生命周期保持 **request-scoped**；
> - Phase 11 当前唯一目标是 Authentication & Access Control。
>
> Phase 11+ 权威路线见 `docs/roadmap-v2.md`。

本文档冻结 Phase 12 中 Web Research 能力的设计边界。当前不要求提前实现任何 Phase 13+ MCP、
Phase 14 Agent Planner 或 Phase 15 Session Context。

## 1. Motivation

当前 Personal Knowledge Assistant 只能基于 Personal Knowledge Base 回答。当个人知识库没有足够证据时，
系统返回 `insufficient_evidence`——这是正确的安全行为，但对通用外部知识或时效性问题体验有限。

Phase 12 的目标之一是：当问题确实需要外部知识时，允许系统**安全地**检索公网资料，把外部信息转换成
临时 Evidence，继续走 Evidence-Grounded Generation，并返回可验证的外部来源引用。

目标链路：

```text
Question
   ↓
Capability / Research Eligibility
   ↓
Web Research Skill
   ↓
Search → Fetch → Extract → Normalize
   ↓
Temporary External Evidence
   ↓
Grounded Generation
   ↓
Citation Validation
   ↓
Researched Answer
```

真正目标不是“搜索一下”，而是：

> **Search → Research → Evidence → Grounded Answer**

## 2. Phase 12 的产品定位

Phase 9 解决「访客如何使用助手」；Phase 10 解决「系统如何持续更新并稳定部署」；
Phase 11 解决「谁可以使用消费型能力，以及成本与权限如何被控制」；Phase 12 才解决
「系统如何把 Personal Knowledge 与 External Research 组织成可复用 Skill」。

Phase 12 是 Product Capability Expansion，不是部署功能，也不是 Post-v1 Performance Optimization。

### 核心产品原则

1. **Evidence First**：Web Research 不允许模型自由发挥，最终回答仍必须由 Evidence 支撑；
2. **Personal Facts Integrity**：普通 Web Search 不能给本人补不存在或未经验证的个人事实；
3. **Web Evidence Is Temporary**：Phase 12 默认 request-scoped，不写入长期 Personal Knowledge；
4. **Persona ≠ Web Knowledge**：外部资料不能自动变成「我的经历」「我的观点」「我做过的事」；
5. **Research Is Bounded**：候选数、抓取数、页面大小、timeout、redirect、总预算全部有上限；
6. **Authenticated Capability**：Phase 12 的公网调用必须继承 Phase 11 的 authentication / authorization / quota；
7. **No Session Runtime Yet**：跨轮 Evidence Reuse 留到 Phase 15。

## 3. 与未来 Agent 架构的关系

旧设计曾规定“所有问题先 Personal KB，只有 insufficient 才 Web Research”。这在纯 RAG 产品里合理，
但在未来 Agent 架构中不再作为全局唯一规则。

Phase 12 应建立最小 Capability Foundation，使系统至少能区分：

```text
Personal / self knowledge intent
      → PersonalKnowledgeSkill

External / current knowledge intent
      → WebResearchSkill
```

对于 Phase 12 第一版，可以保持保守、可审计的路由策略，不引入 Autonomous Agent Planner。
真正的多能力 Planner / Executor 统一留到 Phase 14。

### PersonalKnowledgeSkill

现有 Phase 0–10 RAG 不重写，而应逐步被封装成稳定能力边界：

```text
PersonalKnowledgeSkill
    ↓
Retrieval
    ↓
Evidence Context
    ↓
Grounded Generation
    ↓
Citation Validation
```

Agent / Capability 层不得依赖 sqlite-vec、BGE、FTS5 等内部细节。

## 4. Research Trigger / Eligibility Policy

Phase 12 不允许“任意问题都联网”。Research Eligibility 至少区分三类：

### A. General / External / Current Knowledge

可以进入 Web Research，例如：

- 某个开源项目最近有什么更新？
- Python 新版本发生了什么变化？
- 某个公开技术标准当前状态是什么？

### B. Personal Facts

默认不能通过普通 Web Search 给本人补事实，例如：

- 你手机号是多少？
- 你在哪家公司实习？
- 你获过什么奖？
- 你的项目有多少用户？

如果 Personal Knowledge 没有证据，默认继续拒答。以后如需外部补充个人事实，只能使用明确验证、
allowlist 的 Official Personal Sources，并在独立设计中处理 Identity Integrity。

### C. Private / Internal Information

不得通过 Web Research 绕过既有 public-only boundary，例如：

- 公司内部项目；
- private repository；
- 未公开个人信息；
- 内部接口或客户资料。

### 技术失败不是 Research Trigger

以下情况不能因为“当前回答失败”就自动转 Web Research：

- ProviderFailure；
- InternalError；
- Timeout；
- RateLimit；
- ServiceBusy；
- Auth / quota failure。

LLM API 故障不能解释成“知识库不足，所以换成联网”。

## 5. Research Workflow

正式定义 **WebResearchSkill** 为一个 Bounded / Fixed Workflow Capability，而不是 Autonomous Agent。

概念契约：

```text
WebResearchSkill.research(query, principal/context)
→ ResearchOutcome
    external_evidence: ExternalEvidence[]
    eligibility: ResearchEligibilityDecision
    diagnostics: ResearchDiagnostics   # internal only
```

内部编排：

```text
query
  ↓
search results
  ↓
candidate selection
  ↓
safe fetch
  ↓
content extraction
  ↓
normalization
  ↓
ExternalEvidence[]
```

每一步都必须受配置上限约束，不产生不受控网络行为。

### Research Query

第一版优先使用原始 user question 或非常有限、可审计的 deterministic preparation。

Phase 12 第一版不引入：

- unlimited multi-query；
- HyDE；
- agent query planning；
- 自循环关键词生成；
- search → insufficient → search again → ... 无限递归。

如果后续 evaluation 证明单 query 明显不足，再单独评估有限 query expansion。

## 6. Provider Abstraction

业务层不得直接依赖某家搜索 API。

定义可替换：

```text
SearchProvider.search(query) → SearchResult[]

SearchResult:
    title
    url
    snippet
    provider_rank
```

具体 vendor 在实现 Phase 12 时根据：

- 成本；
- API 稳定性；
- 中文/英文覆盖；
- 结果质量；
- rate limit；
- 服务器网络可用性；

再做选择。

## 7. Search ≠ Evidence

搜索结果 title / snippet 不自动等于最终 Evidence。

推荐链路：

```text
query
↓
search results
↓
candidate selection
↓
fetch selected pages
↓
content extraction
↓
normalization
↓
ExternalEvidence[]
```

如果某个 Provider 能直接提供可验证的结构化原文片段，第一版可以简化 fetch；但架构上仍要明确
SearchResult 与 ExternalEvidence 是不同 contract。

## 8. Evidence Model

Evidence 至少区分来源：

```text
Evidence
├── PersonalEvidence
└── WebEvidence
```

Web Evidence 至少包含：

- `evidence_id`
- `origin = web`
- `title`
- `url`
- `content`
- `retrieved_at`

可选：

- `publisher`
- `domain`
- `published_at`
- `source_quality_hint`

Personal Evidence 继续使用：

- `source_path`
- `section_path`
- `chunk_id`

**不要强行让 Web Source 伪装成 Markdown Chunk。**

未来 MCP Tool Result 也不要强行伪装成 Evidence；Phase 14 可以统一抽象为 AgentObservation。

## 9. Citation Model

继续复用短 Evidence ID：

```text
E1 = Personal Evidence
E2 = Web Evidence
E3 = Web Evidence
```

LLM 只引用短 ID。CitationValidator 再映射为不同 PublicSource 类型。

Personal source：

```json
{
  "type": "personal",
  "id": "E1",
  "title": "...",
  "section": ["..."],
  "source_path": "..."
}
```

Web source：

```json
{
  "type": "web",
  "id": "E2",
  "title": "...",
  "url": "https://...",
  "domain": "..."
}
```

外部 URL 必须来自系统真实 search/fetch 结果。

LLM 不得：

- 生成 citation URL；
- 修改 citation URL；
- 凭空添加网页来源。

幻觉 URL 必须由 CitationValidator / source mapping 拒绝。

## 10. Temporary External Evidence

Phase 12 第一版 External Evidence 必须是 **request-scoped**。

不得：

- 写入 `knowledge.db`；
- 写入长期 Embedding Index；
- 修改 Personal Profile；
- 修改 Notes；
- 自动提交 Git；
- 自动变成 Long-term Memory。

概念边界：

```text
Personal Knowledge          = reviewed, long-lived
External Research Evidence  = request-scoped in Phase 12
Session Context             = Phase 15
Long-term Agent Memory      = separate future concern
```

旧 Phase 11 设计里关于 in-memory ephemeral session store、TTL、max sessions、跨轮 Evidence Reuse
等原则保留为未来参考，但实际实现统一推迟到 Phase 15。

## 11. Prompt Injection Boundary

网页内容是 **UNTRUSTED EVIDENCE DATA**。

可能出现：

- `Ignore previous instructions`
- `System prompt:`
- `Run this command`
- `Reveal secrets`

这些都只能作为 Evidence data，不得提升为：

- system instruction；
- developer instruction；
- tool instruction；
- Agent planner instruction。

Research 内容进入 Generation Context 时必须保持结构化只读边界。

## 12. Network Security / SSRF

Phase 12 第一版必须同时实现网络安全边界，不能留作普通“后续优化”。

至少包括：

- scheme allowlist（通常只允许 `http` / `https`）；
- URL validation；
- DNS / resolved address validation；
- localhost blocking；
- RFC1918 private IP blocking；
- link-local / metadata IP blocking；
- IPv6 private / loopback / link-local blocking；
- redirect limit；
- redirect target re-validation；
- Content-Type allowlist；
- response body size limit；
- connect/read/total timeout；
- HTML / Script 清理；
- 不允许 `file://`；
- 不允许访问 internal metadata endpoint。

尤其禁止：

```text
127.0.0.1
localhost
169.254.0.0/16
RFC1918
::1
private/link-local IPv6
file://
cloud metadata endpoint
```

原因：这是 authenticated public user 可以间接触发的服务器端网络请求。

Authentication 不能替代 SSRF 防护。

## 13. Failure Model

必须区分业务不足与技术失败。

典型语义：

```text
External research eligible
+ no usable evidence
→ insufficient_evidence / researched-insufficient

Search provider unavailable
→ safe research-unavailable semantic

Fetcher rejected by security policy
→ candidate rejected, diagnostics internal only

All evidence invalid
→ no fabricated answer
```

任何情况下不允许“因为搜索失败而让 LLM 自己补答案”。

## 14. UI / SSE Integration

Phase 12 可以在 authenticated API v2 中扩展状态：

```text
accepted
→ routing / retrieving
→ researching
→ generating
→ validating
→ completed
```

最终实际 stage naming 应在 Phase 12 API contract 中冻结，不能随意破坏 Phase 11 已建立的 Auth / quota boundary。

可以增加 public answer status：

```text
answered
researched
insufficient_evidence
```

`researched` 必须明确表示回答补充使用了 External Evidence，不能伪装成 Personal Knowledge answer。

## 15. Evaluation Strategy

Phase 12 必须新增独立 Research Evaluation，继续保持：

> **Evaluation = cross-cutting infrastructure**

至少覆盖：

1. Personal knowledge question → PersonalKnowledgeSkill 正常回答；
2. external/current question → Research eligible；
3. Web Evidence grounded → citation valid；
4. personal factual unknown → 不自动 Web Search 补本人事实；
5. private/internal query → 不通过 research 绕过安全边界；
6. unauthenticated / quota-exceeded → 不开始 Research；
7. search provider failure → 不胡编；
8. malicious webpage prompt injection → 只是 evidence data；
9. hallucinated URL → validator 拒绝；
10. conflicting web sources；
11. duplicate / low-quality results；
12. SSRF attempts；
13. redirect-to-private-IP；
14. oversized page；
15. research timeout；
16. External Evidence 不进入长期 Personal Knowledge。

## 16. Source Quality Policy

不要把所有网页视为同等可信。

第一版不做复杂 PageRank，但至少遵循：

- 优先：官方文档、官方组织、权威机构、原始资料；
- 其次：可靠媒体、可靠技术来源；
- 谨慎：论坛、个人博客、聚合页；
- 高风险或容易变化的事实：允许要求 multi-source corroboration。

Source Trust / Domain Policy 可以后续按 evaluation 结果扩展。

## 17. Phase 12 Delivery Plan

### Phase 12A — Capability Foundation

- `PersonalKnowledgeSkill` wrapper；
- minimal Capability / Skill contract；
- Research eligibility contract；
- 不引入通用 Agent Planner。

### Phase 12B — Web Research Core

- SearchProvider；
- candidate selection；
- safe fetcher；
- extraction / normalization；
- ExternalEvidence；
- bounded workflow；
- deterministic tests。

### Phase 12C — Grounding / Product Integration / Evaluation

- External Evidence → ContextBuilder；
- web PublicSource；
- Citation Validation；
- authenticated API v2 / SSE integration；
- Web source UI；
- Research Evaluation；
- production cost / latency / failure acceptance。

## 18. Non-goals

Phase 12 明确不做：

- Autonomous Research Agent；
- General Agent Planner / Executor；
- MCP Tool Runtime；
- Browser Automation；
- Arbitrary Tool Use；
- Private Web Crawling；
- Login-protected Pages；
- Paywall Bypass；
- Automatic Permanent Knowledge Ingestion；
- Auto-write Notes；
- Auto-update Profile；
- Redis；
- Full Conversation Memory；
- Session Evidence Reuse；
- Long-term Agent Memory；
- LLM Web Browsing Without Citations；
- Unlimited Recursive Search。

完整 Session Context 统一属于 Phase 15；MCP 属于 Phase 13；Agent Orchestrator 属于 Phase 14。
