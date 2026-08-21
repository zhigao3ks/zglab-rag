# Web Research Skill 设计（Phase 11）

Phase 11 — **External Research & Session Evidence**（外部研究与临时会话知识）。

本文档冻结该产品能力的设计边界。当前仅冻结架构与路线，**不实现任何代码**。

## 1. Motivation

当前助手只能基于 Personal Knowledge Base 回答。当个人知识库没有足够证据时，系统返回
`insufficient_evidence` 拒答文本——这是正确的安全行为，但对「通用外部知识」类问题
（例如「红烧肉怎么做？」「某个开源项目最近有什么更新？」）体验有限。

Phase 11 冻结的新能力：当个人知识不足时，允许助手**安全地**检索公网资料，把外部信息
转换为临时 Evidence，继续走 Evidence-Grounded Generation，并返回可验证的外部来源引用。

最终目标链路：

```text
Personal Knowledge First
        ↓
Insufficient Evidence
        ↓
Web Research Skill
        ↓
Temporary External Evidence
        ↓
Grounded Generation
        ↓
Validated External Citations
```

真正目标不是「搜索一下」，而是 **Search → Research → Evidence → Grounded Answer**。
因此该阶段正式名称是 External Research & Session Evidence，而不是 Web Search。

## 2. Product Behavior

### 为什么单独定义 Phase 11

- Phase 9：解决「访客如何使用助手」；
- Phase 10：解决「系统如何持续更新并稳定部署」；
- Phase 11：解决「个人知识不足时，助手如何安全获取外部知识」。

Phase 11 是新的 **Product Capability**，不是 Phase 9 UI 功能，不是 Phase 10 部署功能，
也不是 Post-v1 Performance Optimization。Phase 9 / Phase 10 的责任边界不因本文档改变。

### 核心产品原则（冻结）

1. **Personal Knowledge First**：默认首先使用个人知识库，不得每个问题都直接联网。
2. **External Research Is Fallback**：只有个人知识无法安全回答时，才进入 Web Research。
3. **Web Evidence Is Evidence**：外部网页不能让模型自由发挥，仍然必须
   Evidence First + Citation Validation。
4. **Web Evidence Is Temporary**：默认不得写入 `knowledge.db`、Personal Knowledge、
   Long-term Profile 或 Notes corpus；它只是 request-scoped 或 session-scoped evidence。
5. **Persona ≠ Web Knowledge**：外部资料不能自动变成「我的经历」「我的观点」
   「我做过的事情」。

### 回答体验

触发 External Research 时，回答风格应明确区分「我的知识」和「我刚查到的外部资料」。
推荐产品语义示例：

> 这个我自己的知识库里暂时没有足够信息，不过我查了一些公开资料。

语气可以自然、轻松，但不要把固定句式（例如「哈哈」）硬编码成所有 fallback 模板；
自然程度由 Persona / UX Copy 决定。

## 3. Architecture

概念链路（**固定 Workflow，不是 LLM Autonomous Agent**；第一版不允许模型自己决定
何时随意调用工具）：

```text
User Question
      ↓
Personal Knowledge Retrieval
      ↓
GroundedAnswerService
      │
      ├── ANSWERED
      │      ↓
      │   Return personal-grounded answer
      │
      └── INSUFFICIENT_EVIDENCE
             ↓
       Research Eligibility Policy
             ↓
       Web Research Skill
             ↓
       External Evidence
             ↓
       Temporary Evidence Context
             ↓
       Grounded Generation
             ↓
       Citation Validation
             ↓
       Researched Answer
```

推荐的未来模块语义（本次任务**不创建**这些文件，只冻结边界）：

```text
src/zglab_rag/research/
├── contracts.py     # ResearchQuery / SearchResult / ExternalEvidence / ResearchOutcome
├── service.py       # WebResearchSkill 固定 workflow 编排
├── policy.py        # Research Eligibility Policy
├── providers/       # SearchProvider 实现（vendor 可替换）
├── fetching/        # 页面抓取与网络安全边界（SSRF 防护）
└── extraction/      # 内容提取与规范化
```

Phase 11 是扩展 Evidence Source，不是绕过 Phase 8 Grounding 基本原则。

## 4. Trigger Policy

第一版不引入复杂的 LLM Router 或 Agent Planner，优先复用 Phase 8 已有的明确状态：

- 只有 Personal Grounded Generation 返回
  `GenerationStatus.INSUFFICIENT_EVIDENCE` 时，才有资格进入 External Research
  Fallback。

以下情况**绝不能**触发 Web Research：

- `ProviderFailure`
- InternalError
- Timeout
- RateLimit
- ServiceBusy

例如 LLM API 故障不能解释成「知识库不足，所以去联网」。

其他冻结规则：

- **Fallback 不允许无限递归**：固定最多 Personal KB → Web Research → Generation
  一次链路；第一版最多一次 Research Workflow，不允许
  search → insufficient → search again → … 的循环。未来高级 Research Agent 另立能力。
- **Research Query**：Phase 11A 第一版优先使用原始 user question，或非常有限、
  可审计的 deterministic preparation；不引入 multi-query、HyDE、agent query
  planning 或无限关键词生成，后续效果不足再评估。

## 5. Research Eligibility Policy

重要安全边界：**并不是所有 insufficient query 都自动联网**。至少区分三类：

**A. General / External Knowledge**（可以进入 Web Research）

例如：红烧肉怎么做？Python 新版本有什么变化？某个开源项目最近有什么更新？

**B. Personal Facts**（默认继续拒答）

例如：你手机号是多少？你在哪家公司实习？你获过什么奖？你的项目有多少用户？

如果 Personal KB 没有证据，默认继续拒答，不通过普通 Web Search 给「黄志高本人」
补事实。原因：同名人物污染、搜索结果不可靠、Persona Identity Integrity。
以后如需外部补充个人事实，只能使用明确验证或 allowlist 的
**Official Personal Sources**。

**C. Private / Internal Information**（不得绕过安全边界）

例如：公司内部项目、private repository、未公开个人信息。不得通过 Web Research
绕过 public-only security boundary。

## 6. Skill Contract

正式定义 **WebResearchSkill**：它是一个 **Fixed Workflow Capability**，不是 Agent。

概念契约（实现时确定最终字段）：

```text
WebResearchSkill.research(query)
→ ResearchOutcome
    external_evidence: ExternalEvidence[]
    eligibility: ResearchEligibilityDecision
    diagnostics: ResearchDiagnostics   # 仅内部，不出公网
```

Skill 内部编排 search → candidate selection → fetch → extraction → normalization，
全部步骤受配置上限约束（候选数、抓取数、页面大小、超时），不产生不受控的网络行为。

## 7. Provider Abstraction

Phase 11 实现时应定义 `SearchProvider`，而不是业务逻辑直接依赖某家搜索 API：

```text
SearchProvider.search(query) → SearchResult[]

SearchResult:
    title
    url
    snippet
    provider_rank
```

后续可以替换不同搜索服务。**本次文档冻结不确定具体 vendor**，未来实现时再评估。

## 8. Search → Fetch → Extract → Evidence

必须明确：**Search ≠ Evidence**。搜索结果 snippet 不一定就是最终 Evidence。

推荐研究链：

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

第一版可以根据 Provider 能力简化（例如仅使用结构化 snippet 来源），但架构上不要把
Search Result Title + Snippet 直接等同于可信网页全文。

## 9. Evidence Model

未来 Evidence 应能区分来源：

```text
EvidenceItem
    origin: personal | web
```

Web Evidence 至少需要：

- `evidence_id`
- `origin = web`
- `title`
- `url`
- `content`
- `retrieved_at`

可选：`publisher`、`domain`。

Personal Evidence 继续使用 `source_path` / `section_path` / `chunk_id`。
**不要强行让 Web Source 伪装成 Markdown Chunk**。

## 10. Citation Model

继续复用 Phase 8 的短 Evidence ID（E1、E2、E3…）：模型只引用短 ID，例如
E1 = Personal KB、E2 = Web Source、E3 = Web Source；CitationValidator 再把短 ID
映射成不同类型的 PublicSource。

未来 Public Source 支持两种类型：

```json
{
  "type": "personal",
  "id": "E1",
  "title": "...",
  "section": ["..."],
  "source_path": "..."
}
```

```json
{
  "type": "web",
  "id": "E2",
  "title": "...",
  "url": "https://...",
  "domain": "..."
}
```

外部 URL 必须来自系统实际检索结果。LLM 不得：

- 生成 citation URL；
- 修改 citation URL；
- 凭空添加网页来源。

幻觉 URL 由 CitationValidator 拒绝（见 Evaluation）。

## 11. Temporary / Session Evidence

用户目标：「将网络搜到的信息整理为当次会话的知识源」。正式定义为
**Temporary / Session Evidence**。

第一版必须：

- 不进入 `knowledge.db`；
- 不进入长期 Embedding Index；
- 不改变 Personal Profile；
- 不改变 Notes；
- 不自动提交 Git；
- 请求或 session 结束后可以丢弃。

概念区分：

```text
Personal Knowledge            = Long-lived
External Research Evidence    = Temporary
Conversation Memory           = Separate Future Concern
```

### Session Store 原则（Phase 11B）

未来如实现 session 复用，优先 Lightweight Ephemeral Store。单实例初版可以 in-memory，
并限制 TTL、max sessions、max evidence items、max bytes。不要一开始引入 Redis，
除非生产规模明确需要。刷新或 session 失效后，临时 evidence 可以消失；不自动变成
长记忆。

## 12. Prompt Injection Boundary

必须继承 Phase 8 Prompt Injection Boundary。网页中可能存在
「Ignore previous instructions」「System prompt:」「Run this command」「Reveal secrets」
等内容，这些都只能作为 **UNTRUSTED EVIDENCE DATA**，不得提升为：

- system instruction；
- developer instruction；
- tool instruction。

研究内容进入 LLM Context 时，必须保持明确的结构化边界（与 personal evidence 相同的
只读数据标注）。

## 13. Network Security / SSRF

Phase 11 实现时必须考虑：

- HTML / Script 清理；
- 页面大小限制；
- Fetch timeout；
- Redirect limit；
- Content-Type allowlist；
- URL validation；
- Localhost / private IP blocking；
- SSRF protection。

尤其禁止 Web Research Fetch：`127.0.0.1`、`localhost`、`169.254.x.x`、RFC1918
private network、`file://`、internal metadata endpoint。原因：这是公网用户可以
间接触发的网络请求。本次只冻结要求，不实现。

## 14. Failure Model

必须定义组合失败语义：

- **Personal Insufficient + Web Research Unavailable → `insufficient_evidence`**，
  而不是 INTERNAL_ERROR——因为 Personal Knowledge 不足本身是真实业务状态。
- Research provider 技术性失败的内部 diagnostics 记录
  `RESEARCH_PROVIDER_UNAVAILABLE`；公网给友好语义，例如：
  「我自己的知识库里暂时没有足够信息，外部资料检索目前也不可用。」
- 任何情况下不虚构答案。

## 15. UI / SSE Future Integration

Phase 11 接入当前 Phase 9 SSE 时，允许增加状态 `researching`：

```text
accepted → retrieving → researching → generating → validating → completed
```

未来可以考虑区分 public answer status：`answered` / `researched` /
`insufficient_evidence`。`status = researched` 表示 Personal Knowledge 不足、但
使用 public web evidence 成功回答；**不要把 researched 伪装成 personal grounded
answer**，UI 可以明确告诉用户「本回答补充使用了外部公开资料」。

注意：当前 Phase 9C 的 SSE contract **不提前修改**；本节只记录 Phase 11 将扩展
SSE contract。

## 16. Evaluation Strategy

Phase 11 必须新增独立 Research Evaluation，继续保持 **Evaluation = 基础设施**。
未来评测至少覆盖：

1. Personal KB sufficient → 不联网；
2. 「红烧肉怎么做」→ personal insufficient → research triggered；
3. Web evidence grounded → citations valid；
4. Personal factual unknown → 不自动 web search 补个人事实；
5. Private / internal query → 不通过 research 绕过安全边界；
6. Search provider failure → 不胡编；
7. Malicious webpage prompt injection → 只是 evidence；
8. Hallucinated URL → CitationValidator 拒绝；
9. Conflicting web sources；
10. Duplicate / low-quality results；
11. SSRF attempts；
12. Research timeout。

## 17. Non-goals

第一版明确不做：

- Autonomous Research Agent；
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
- LLM Web Browsing Without Citations；
- Unlimited Recursive Search。

## 18. Phase 11A / 11B Delivery Plan

为避免一次复杂度过高，冻结两个主要子阶段（11C 视集成验收职责需要保留）：

### Phase 11A — Web Research Fallback

范围：单次 request 中 Personal Insufficient → Web Research → Temporary Evidence
→ Answer。Evidence 生命周期为 **request-scoped**；不要求完整多轮记忆。

### Phase 11B — Session Evidence Reuse

允许在同一浏览器 session 内临时复用已经研究过的 Web Evidence。例如第一问
「红烧肉怎么做？」、第二问「那需要放八角吗？」。11B 才考虑 Session Evidence +
有限 Conversation Reference Context，以及 §11 的 ephemeral session store 原则。

### Phase 11C — Product Integration / Evaluation（视职责需要）

SSE `researching` 状态与 `researched` public status 的产品集成、Web UI 展示
（外部来源 UI、与个人来源的区分）以及 Research Evaluation 作为验收基础设施。

### 来源质量策略（原则）

不要把所有网页视为同等可信。未来允许引入 Source Trust / Domain Policy，但第一版
不做复杂 PageRank。基本原则：

- 优先：官方文档、官方组织、权威机构、原始资料；
- 其次：可靠媒体、可靠技术来源；
- 谨慎使用：论坛、个人博客、聚合页；
- 高风险事实：未来可以要求 multi-source corroboration。
