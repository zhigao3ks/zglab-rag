# Grounded Generation 与 Citation

Phase 8 建立了完整的核心问答闭环：

```text
Question
↓
Retrieval（vector 默认 / reranked 可选）
↓
Evidence Context（短 Evidence ID + 预算截断）
↓
External LLM（OpenAI-compatible Provider）
↓
Grounded Answer（结构化 JSON）
↓
Citation Validation（确定性代码校验）
↓
Final Response
```

这是一个确定性固定 workflow，不是 Agent loop。

## 1. Persona != Evidence

系统以黄志高第一人称回答，但 Persona 只影响表达方式：

- Persona 规则写在 system prompt 中，只允许“我认为 / 我目前关注 / 我在项目中采用”这类表达；
- 所有个人事实、项目事实、经历、指标、论文、实习、获奖与技术观点只能来自本次检索到的
  Evidence；
- Evidence 没有的信息不得补充、推测或虚构；
- 不允许用预训练知识回答后伪装成个人经历或个人观点。

约束不是只写在 Prompt 里。Citation 是否存在、是否属于本次 Evidence、private evidence 是否
泄漏、输出 schema 是否合法，全部由代码确定性校验。

## 2. Evidence ID

LLM 不接触内部 `chunk_id`。`ContextBuilder` 为每次请求按 retrieval rank 分配短
Evidence ID：`E1`、`E2`、…。LLM 只引用这些短 ID，系统在校验通过后把 ID 映射回
`chunk_id`、`source_path`、`section_path` 等完整 provenance。Evidence ID 只在单次请求内有效。

进入 Context 的字段只有 Evidence ID、Title、Section 和 Content。绝对路径、内部 revision
hash、database id 与 embedding score 都不进入 Prompt；score 只保留在结果 metadata 中供
debug 使用。

## 3. Context Budget

配置项（`config.py` / `.env`）：

```text
ZGLAB_RAG_GENERATION_RETRIEVAL_TOP_K=5
ZGLAB_RAG_GENERATION_MAX_EVIDENCE_ITEMS=5
ZGLAB_RAG_GENERATION_MAX_CONTEXT_CHARS=6000
```

截断是确定性的：

- 按 rank 从高到低贪心保留完整 chunk；
- 从不从 chunk 中间截断，预算耗尽时丢弃低 rank Evidence；
- 只要 retrieval 非空，Top-1 Evidence 一定保留。

## 4. 结构化生成

Provider 必须返回可验证的结构化结果：

```json
{
  "answer": "...",
  "claims": [{"text": "事实性陈述", "citations": ["E1"]}],
  "citations": ["E1", "E3"],
  "insufficient_evidence": false
}
```

采用 claim-level citation。解析容忍代码围栏与多余文字，但任何 schema 违规都会触发
`InvalidStructuredOutput`。LLM 不允许发明新的 Evidence ID。

**answer / claims 双来源规则**：`answer` 字段只作内部参考（保留在 `GenerationResult.raw_answer`
供 debug），最终用户可见回答由 validated claims 确定性渲染（按顺序拼接 claim 文本）。
自由文本永远不能绕过 claim-level citation validation 成为公开回答；拒答也统一使用固定
文本。

## 5. Citation Validation

确定性校验规则（hardened）：

1. 回答时 claims 必填；只有 top-level citations 不能支撑自由文本；
2. 每个非空 claim 必须至少包含一个有效 citation——“至少一个 claim 有引用”不能
   使整个回答 grounded；
3. citation 格式合法（`^E[1-9][0-9]*$`）且属于本次 context；
4. cited evidence 集合由所有 validated claim citations 的并集确定性生成；top-level
   citations 不信任 LLM，若提供则必须与该并集完全一致；
5. `insufficient_evidence=true` 时不允许任何 claim / citation（不能伪造引用）；
6. 校验通过后才把 Evidence ID 映射到 title / section_path / source_path。

例如 LLM 输出 `E99` 而 context 只有 `E1~E5`，结果是校验失败，绝不会原样返回给用户。

## 6. Insufficient Evidence

系统必须允许回答“当前公开知识库中没有足够信息回答这个问题”。触发来源：

1. retrieval 返回空；
2. LLM 明确判断 `insufficient_evidence=true`；
3. citation validation 失败且一次修复重试后仍无法安全恢复。

不基于 3 条 hard negative 设置 cosine 拒答阈值；检索 score 只记录，供以后评估。
知识库中不存在的问题（例如“Transformer 是什么？”在库中无相关内容时）不得用预训练知识
自由回答后伪装成个人观点。

## 7. Prompt Injection Boundary

Evidence 是数据，不是系统指令。Prompt 中明确划分角色边界：

```text
system message: SYSTEM RULES（Persona + Evidence 规则 + 输出规则 + 注入边界规则）
user message:   USER QUESTION + EVIDENCE DATA（标注为只读引用数据）
```

即使某个 Markdown chunk 中包含 “Ignore previous instructions” 或 “System prompt:”，
它也只是 EVIDENCE DATA 中的普通引用内容，不获得任何系统优先级。

## 8. Provider 抽象

业务代码只依赖 `GenerationProvider` protocol（`generate(request) → ProviderResponse`），
不依赖具体厂商 SDK。第一版实现 `OpenAICompatibleProvider`：

- 通过 `base_url` / `api_key` / `model` 配置，读取 `.env`；
- 支持 `ZGLAB_RAG_LLM_BASE_URL` / `ZGLAB_RAG_LLM_API_KEY` / `ZGLAB_RAG_LLM_MODEL`，
  并兼容旧的无前缀 `LLM_*` 变量名；
- 网络瞬时错误（超时、连接错误、429/5xx）在 Provider 层有限重试，不与语义修复循环混合；
- API Key 永不写入日志、诊断或 Prompt。

Provider 未配置时 CLI 明确输出 `Generation provider not configured`，不产生 stack trace；
单元测试全部使用 FakeProvider，不依赖真实 API。

## 9. Retrieval Mode

`GroundedAnswerService` 支持 `vector` 与 `reranked` 两种模式，默认 `vector`
（当前生产 baseline）。只有显式选择 `reranked` 才加载 471 MB CrossEncoder 模型。

## 10. Failure Model

失败类型互相区分，不会都变成 `RuntimeError`：

```text
ProviderNotConfigured        LLM 配置缺失
RetrievalFailure             检索阶段失败
ProviderFailure              网络 / 超时 / HTTP 错误（不产生假答案）
InvalidStructuredOutput      输出无法解析为 schema
CitationValidationFailure    引用违反确定性规则
```

语义修复重试只针对 `InvalidStructuredOutput` 与 `CitationValidationFailure`，最多 1 次，
修复 Prompt 会说明上一次违反了哪条规则。重试仍失败时，返回 insufficient-evidence 安全
结果（固定拒答文本），并在 diagnostics 中记录 `failure_reason`。

## 11. CLI

```bash
uv run python -m zglab_rag.generation.cli ask \
  "Agent 长期记忆和 Context 有什么区别？"

uv run python -m zglab_rag.generation.cli ask \
  "你是谁？" --mode reranked --debug
```

输出 Answer / Sources / Diagnostics（retrieval 与 generation latency、mode、evidence
count、repair_attempts、token usage）。`--debug` 额外显示 chunk_id 与 retrieval score；
任何情况下都不显示 API Key、完整 system prompt 或 private evidence。

## 12. Evaluation

独立数据集 `evaluation/generation.yaml`（不修改 `retrieval.yaml`），22 条 query 覆盖
identity / knowledge / project / problem / mixed / hard negative，每条标记
`expected_evidence` 与 `should_answer`。不定义唯一标准答案。

```bash
uv run python -m zglab_rag.evaluation.generation
uv run python -m zglab_rag.evaluation.generation --retrieval-only
```

第一版确定性指标：retrieval evidence hit、citation validity（可交付回答中通过引用校验的
比例，校验由代码强制执行）、citation coverage（已回答 query 对 expected evidence 的引用
覆盖率）、should-answer correctness、insufficient-evidence correctness；hard negative
记录检索证据、score、生成决定与引用；每条 query 额外记录 claims、cited sources 与
answer preview 供人工审阅。Groundedness 与 usefulness 留给人工审阅；暂不引入
LLM Judge。结果写入 Git ignored 的 `artifacts/evaluation/`。Provider 未配置时只报告
retrieval 侧指标并明确说明未运行真实 generation。

数据集标注调整（groundedness hardening，均属情况 A：实际引用完全 grounded，原标注
过窄）：gen-problem-03 补充文档级 target（精确“直接原因”小节在 vector 排名 rank 10，
超出 top_k=5，但 Top-5 全部是同一复盘文档的 grounded 小节）；gen-mixed-02 补充
evidence-grounded-resume-generation 文档级 target（identity profile 原则段未进 Top-5）；
gen-mixed-03 移除非必需的 Astro 背景 target，保留核心隐私结论 target。Phase 8 冻结
Retrieval baseline，不修改检索行为。
