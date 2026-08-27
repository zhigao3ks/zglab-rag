# Phase 12 — Web Research Evaluation

> 日期：2026-08-26 ｜ Harness：`src/zglab_rag/evaluation/web_product.py` ｜
> Dataset：`evaluation/web-product.yaml`（37 题 / 6 类）｜ 全 offline、
> 确定性（FakeSearchProvider + MockTransport + 合规 fake LLM）。

## 1. Dataset 构成

| 类别 | 题数 | 冻结策略下的期望 |
| --- | --- | --- |
| personal | 10 | PERSONAL（自我指涉优先） |
| current | 8 | WEB（时新意图） |
| external_stable | 8 | PERSONAL（v1 保守：稳定外部知识不联网） |
| ambiguous | 6 | PERSONAL（模糊一律不产生搜索成本） |
| no_result | 2 | WEB（且必须 zero-LLM insufficient） |
| adversarial | 3 | WEB（prompt-injection fixture） |

`expected_capability` 记录的是**冻结的 v1 确定性策略**行为，不是理想
Router 的行为；Phase 14 可能替换该策略，此数据集负责锁定 12D 边界与
false-web-trigger 成本。

## 2. 真实测量结果（2026-08-26 本地运行）

```text
dataset_size=37
selection_accuracy=1.000
false_web_trigger_rate=0.000
false_personal_trigger_rate=0.000
answer_success_rate=0.846
citation_valid_rate=0.846
provenance_valid_rate=1.000
injection_isolation_ok=True
zero_evidence_no_llm=True
search_calls_per_request=[1]
total_search_api_calls=13
total_llm_calls=11
```

解读：

- **Selection**：37/37 与冻结策略一致；false-web-trigger=0 说明
  personal/external_stable/ambiguous 题不产生任何 Search API 成本；
- **answer_success_rate=0.846**：13 个 web 题中 11 题 answered；2 题
  no_result 合法地返回 insufficient（这正是期望行为，不是失败）；
- **citation_valid_rate**：所有 answered 结果均通过 Phase 8 硬 citation
  gate；unanswered 项无 citation 可言；
- **provenance_valid_rate=1.000**：全部 source URL 来自 research
  provenance；LLM（fake）无法引入任何 URL；
- **injection_isolation_ok=True**：3 条 adversarial fixture 的注入文本只
  位于 UNTRUSTED 数据区，未进入 system prompt，未产生伪造 source；
- **zero_evidence_no_llm=True**：no_result 题 LLM 调用计数=0；
- **成本**：每 web 请求恰好 1 次 search 调用；本次评估总计 13 次 search、
  11 次 LLM 调用（均为 fake，无真实费用）。

## 3. 复现命令

```bash
uv run python -m zglab_rag.evaluation.web_product
# 报告写入 artifacts/evaluation/web-product-<UTC timestamp>.json
```

## 4. 未覆盖项（如实）

- Real Tavily smoke：**NOT RUN**（当前环境无 SEARCH_API_KEY），列为生产
  前置；不得以 offline 结果冒充真实 provider 行为；
- 端到端延迟/生产并发指标留待生产验收采集。
