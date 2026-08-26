# Phase 12C — Web Evidence + Grounded Generation Integration 验收

> 日期：2026-08-26 ｜ 范围：仅内部能力集成；未改动公网 API/SSE；未 commit/push/部署。

## 1. 交付内容

```text
ExternalEvidence[]（12B）
    → adapt_external_evidence（research/web_adapter.py，W→E 确定性映射）
    → build_web_context（generation/context.py，UNTRUSTED 标注 + 第三人称 prompt）
    → generate_from_context（generation/service.py，从 Phase 8 原样抽出的共享管线）
    → Citation Validation（generation/citation.py，零改动复用）
    → CapabilityResult(origin=WEB)
```

- `WebResearchSkill.answer()`：research → evidence → generation → validated
  answer 的完整内部链路；ResearchService 保持纯研究（不调 LLM）；
- contracts additive 扩展：`EvidenceItem` / `AnswerSource` 新增
  `origin/url/domain`，chunk 字段改可选（web 证据不伪造 chunk 身份）；
- `EvidenceOrigin` 下沉至 `generation/contracts.py`，capabilities re-export；
- `ProductionRuntime.web_research_skill` 懒接线（仅 kill switch 打开时构造）。

## 2. 验收 Gates 对照

| Gate | 结论 | 证据 |
| --- | --- | --- |
| 1 Web Evidence 进入现有 Grounded Generation | ✅ | `test_two_web_evidence_produce_grounded_answer_with_web_sources` |
| 2 不复制 Web Generation pipeline | ✅ | 唯一生成入口 `generate_from_context`，无 WebLLM/WebPromptBuilder 副本 |
| 3 Citation Validation 复用 | ✅ | validator 零改动；`test_unknown_citation_is_rejected_then_repaired` |
| 4 Web URL 只来自 provenance | ✅ | source url/domain 由 `resolve_sources` 从 evidence 解析 |
| 5 LLM 无法创建新 source URL | ✅ | `test_model_generated_urls_never_become_sources` |
| 6 Prompt 中 Web Evidence 明确 untrusted | ✅ | system/user prompt 双重 UNTRUSTED 标注；`test_web_context_labels_evidence_untrusted` |
| 7 网页指令不进入 system instruction | ✅ | `test_injection_content_stays_in_evidence_block_only` |
| 8 ExternalEvidence 与 Personal Chunk 概念隔离 | ✅ | web 证据 chunk_id/document_id=None、origin=WEB |
| 9 Search snippet 不作为最终 Evidence | ✅ | 仅 fetch+extract 成功的页面进入 generation（12B 语义延续） |
| 10 zero evidence 不调用 LLM | ✅ | `test_zero_evidence_never_calls_llm`、`test_no_usable_evidence_never_calls_llm`（provider 调用计数=0） |
| 11 partial research evidence 可生成 | ✅ | 12B partial-success 语义 + 单证据端到端测试 |
| 12 unknown citation 被拒绝/修复 | ✅ | repair 成功与耗尽预算两条测试 |
| 13 PersonalKnowledgeSkill 完整回归 | ✅ | 全量 462 测试通过（含 12A capability 测试） |
| 14 Personal 路径不依赖 SearchProvider | ✅ | `test_personal_skill_path_stays_independent_of_web_research`；默认 `web_research_enabled=false` |
| 15 API v2 默认行为不变 | ✅ | api/ 未改动；`test_public_api.py` 全通过 |
| 16 SSE 公网契约不变 | ✅ | sse.py 未改动；`test_public_sse.py` 全通过 |
| 17 AuthN/AuthZ/CSRF/quota 不变 | ✅ | auth/ 与 gate 代码未触碰 |
| 18 未新增公开 research API | ✅ | 无新 endpoint |
| 19 未实现 Planner | ✅ | 无路由/选择逻辑 |
| 20 未实现 MCP | ✅ | — |
| 21 未实现 Session Evidence | ✅ | — |
| 22 Phase 0–12B 回归全部通过 | ✅ | 462 passed |

## 3. 测试

新增 `tests/test_web_grounding.py`（17 个测试，全 offline）：

- Evidence Mapping：origin/title/url provenance 保留；W→E 映射确定性
  （乱序输入仍按 W 编号排序）；max_items 边界；
- Generation：双证据 grounded answer 与 web sources；zero evidence /
  no usable evidence → LLM 调用计数 0；研究 provider 不可用 → FAILED
  （区别于业务 insufficient）；generation provider 错误 → FAILED；
  unknown citation repair 成功 / 预算耗尽；kill switch fail-closed；
- Prompt Injection：fixture 注入 payload 只出现在 UNTRUSTED 数据区，
  system prompt 不含注入内容；
- Provenance：LLM 输出中的 `https://evil.example` 不成为 source；
- Regression：personal prompt/context 无 web 标记、personal source
  origin=personal/url=None；内部 progress 阶段 request-scoped。

最终验证：

```text
uv run pytest -q          → 462 passed（445 回归 + 17 新增）
uv run ruff check .       → All checks passed
git diff --check          → exit 0
cd web && npm test --run  → 69 passed（未改前端，回归保险）
cd web && npm run build   → 成功
```

## 4. 文件变更

```text
新增  src/zglab_rag/research/web_adapter.py
新增  tests/test_web_grounding.py
新增  docs/web-evidence-grounding.md
新增  docs/evaluations/phase-12c-web-evidence-grounding.md
修改  src/zglab_rag/generation/{contracts,citation,context,persona,service}.py
修改  src/zglab_rag/capabilities/contracts.py（EvidenceOrigin re-export）
修改  src/zglab_rag/research/skill.py（answer 路径 + 工厂）
修改  src/zglab_rag/api/runtime.py（懒接线）
更新  README.md / docs/{architecture,development-plan,roadmap-v2,
      web-research-skill,capability-architecture}.md
```

## 5. Remaining Risks

- DNS rebinding / TOCTOU（12B 记录，保持至真正修复；12D 生产验收 Gate）；
- 真实 Search Provider smoke test 留 12D；
- 公网 response contract（web source 是否暴露 url/domain 字段）留 12D 决定，
  本阶段仅内部模型支持。

## 6. Phase 状态

```text
Phase 12A ✅   Phase 12B ✅   Phase 12C ✅   Phase 12D ⏳（未开始）
```
