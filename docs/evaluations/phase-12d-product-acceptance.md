# Phase 12D — Web Research Product Integration 验收（本地）

> 日期：2026-08-26 ｜ 范围：本地实现与验收。**生产未开启**
> （`WEB_RESEARCH_ENABLED=false`）；真实 provider smoke 与生产验收是独立
> 前置步骤，见文末 Production Prerequisites。未 commit/push/部署。

## 1. Acceptance Gates 对照（§43）

| Gate | 结论 | 证据 |
| --- | --- | --- |
| 1 安全选择 Personal/Web | ✅ | `test_web_product_api.py::TestProductSelection` |
| 2 deterministic、无 LLM Router | ✅ | `capabilities/selection.py`；`test_capability_selection.py` |
| 3 ambiguous 不浪费 Web Search | ✅ | DEFAULT_PERSONAL 规则；evaluation false-web-trigger=0 |
| 4 API v2 向后兼容 | ✅ | mode 默认 auto；旧请求契约测试通过 |
| 5 Web sources 有 origin/url/domain | ✅ | `test_explicit_web_executes_web_skill` |
| 6 URL 只来自 provenance | ✅ | 12C 锁定 + `FakeWebSkill` provenance 断言 |
| 7 Web SSE researching 生效 | ✅ | `test_sse_web_path_emits_researching_stage` |
| 8 Personal SSE 不退化 | ✅ | `test_sse_personal_path_never_emits_researching` + 回归 |
| 9 Web UI 安全外链 | ✅ | `components.test.ts`：target=_blank rel=noopener noreferrer |
| 10 无 raw HTML injection | ✅ | 无 v-html；untrusted title 转义测试 |
| 11 AuthN 在 selection 前 | ✅ | `test_anonymous_never_reaches_selection` |
| 12 AuthZ 在 selection 前 | ✅ | resolve_session 只接受 ACTIVE（gate 复用） |
| 13 CSRF 在 selection 前 | ✅ | `test_csrf_failure_never_reaches_selection` |
| 14 Web permission 服务端执行 | ✅ | `test_admin_only_policy_*`（CAPABILITY_DENIED） |
| 15 Web 独立 quota | ✅ | `web_usage` 表；`TestWebQuotaBoundary` |
| 16 Web concurrency bounded | ✅ | `app.state.research_guard`（默认 1） |
| 17 kill switch 生效 | ✅ | CAPABILITY_DISABLED 测试 |
| 18 Web off 不影响 Personal | ✅ | `test_personal_fully_works_while_web_disabled` + 全量回归 |
| 19 SearchProvider down 不影响 Personal | ✅ | web 失败映射 PROVIDER_UNAVAILABLE；personal 路径零依赖 |
| 20 Search API Key 不进 Git | ✅ | 仅 env example 空值；`.env` ignored |
| 21 Search max calls = 1 | ✅ | 12B 语义延续；evaluation search_calls_per_request=[1] |
| 22 fetch/timeout/size 上限有效 | ✅ | ResearchBudget 原样传递 |
| 23 DNS rebinding blocker 已解决 | ✅ | pinned resolution（§6 下述证据） |
| 24 prompt injection boundary 保持 | ✅ | 12C 测试 + evaluation adversarial（injection_isolation_ok=true） |
| 25 fabricated citation URL 不入 sources | ✅ | 12C provenance 测试 + evaluation provenance_valid=1.0 |
| 26 no evidence 不调用 LLM | ✅ | `zero_evidence_no_llm=true`；API 层 no_result 测试 |
| 27 evaluation dataset 已建立 | ✅ | `evaluation/web-product.yaml`（37 题 / 6 类） |
| 28 capability selection 有测量 | ✅ | accuracy=1.000（37/37） |
| 29 citation validity 有测量 | ✅ | citation_valid_rate=0.846（=answered 占比；no-result 项合法地不回答） |
| 30 cost/latency 基础数据 | ✅ | `artifacts/evaluation/web-product-*.json` |
| 31 真 provider smoke 如实记录 | ✅ | **NOT RUN**（环境无 Key；列为生产前置） |
| 32 production migration runbook | ✅ | `docs/web-research-product.md` §8 |
| 33 rollback 仅 kill switch | ✅ | `docs/web-research-product.md` §9 |
| 34 不存在 MCP | ✅ | — |
| 35 不存在 Agent Planner | ✅ | selection 是确定性小策略 |
| 36 不存在 Session Context | ✅ | — |
| 37 全量 regression | ✅ | 511 passed（Phase 0–12C 462 + 12D 新增 49：rebinding 7 + selection 22 + product API 20） |

## 2. DNS Rebinding 证据（Gate 23）

- 实现：`src/zglab_rag/research/pinned_transport.py`（PinnedHosts +
  PinnedResolutionBackend + build_pinned_transport），`SafeFetcher` 每跳
  验证后 pin，生产路径默认启用；
- 测试：`tests/test_research_rebinding.py`（7 条，全 offline，真实
  httpx/httpcore 栈）——validate 解析公网 A、rebinding 后 connect 只能到
  A 而非私网 B；unpinned host 连接前拒绝；HTTPS SNI 保持 hostname；
  redirect 逐跳重验重 pin；
- 未使用 `verify=False`；未用"第二次 DNS check"充数。

## 3. Prompt Injection Product 测试（§26）

端到端 adversarial fixture（Research → Evidence → Prompt → Grounded
Answer）在 12C `test_web_grounding.py` 与 12D evaluation（3 条 injection
fixture）中覆盖：注入文本只出现在 UNTRUSTED 数据区、不进 system prompt、
不产生伪造 source URL。**验收措辞**：结构化边界在测试锁定的场景下成立；
这不声称"完全防御所有 prompt injection"。

## 4. Production Prerequisites（未完成，如实记录）

1. Real Tavily smoke：NOT RUN（当前环境无 SEARCH_API_KEY）；
2. 生产部署与 `WEB_RESEARCH_ENABLED=true`：未执行（本任务禁止部署）；
3. Authenticated Web 浏览器端到端验收：留待生产迁移时执行。

## 5. Phase 状态

```text
Phase 12A ✅  Phase 12B ✅  Phase 12C ✅  Phase 12D ✅ local
Phase 12 production accepted: NO
```
