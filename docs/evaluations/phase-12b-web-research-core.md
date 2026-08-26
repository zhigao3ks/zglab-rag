# Phase 12B — Web Research Core 验收

日期：2026-08-26
范围：仅 Phase 12B（独立、bounded、受控的 Web Research Pipeline）。
12C（Evidence + Grounded Generation 集成）/ 12D（产品集成与评测）未开始。

## 1. Implementation

| 交付项 | 位置 |
| --- | --- |
| Contracts（SearchProvider / SearchResult / ExternalEvidence / ResearchResult / ResearchBudget） | `src/zglab_rag/research/contracts.py` |
| URL 规范化 + SSRF / DNS / IP 安全 | `src/zglab_rag/research/url_safety.py` |
| Tavily adapter + FakeSearchProvider + deterministic candidate selection | `src/zglab_rag/research/search.py` |
| Safe Fetch（手工 redirect、bounded read、content-type 白名单） | `src/zglab_rag/research/fetch.py` |
| 确定性 HTML/text 抽取与规整 | `src/zglab_rag/research/extract.py` |
| ResearchService 管线编排 | `src/zglab_rag/research/service.py` |
| WebResearchSkill + runtime factory | `src/zglab_rag/research/skill.py` |
| 配置（fail-closed kill switch、预算、key） | `src/zglab_rag/config.py` |
| 运行时文档 | `docs/web-research-runtime.md` |

核心代码约 800 行（含注释），无 Agent / Planner / LLM 参与；新增依赖
`beautifulsoup4`、`httpx`（升为直接依赖）。

明确未做（边界守住）：未接 Grounded Generation、未改 Citation（E1/E2）、
未改 `/api/v2/ask` 与 SSE、未新增 HTTP endpoint、未写 knowledge.db、
未注册 `web_research` 进 CapabilityRegistry、无 MCP / Planner / ReAct。

## 2. Tests（真实执行，全部 offline / deterministic）

```text
uv run pytest tests/test_research_safety.py tests/test_research_fetch.py \
              tests/test_research_pipeline.py -q
→ 93 passed（1.75s）

uv run pytest -q
→ 445 passed（352 Phase 0–12A 回归 + 93 新增）

uv run ruff check . → All checks passed
git diff --check    → 无真实空白问题（既有 CRLF 基线文件按基线处理）
前端                → 本阶段零前端改动
```

### SSRF 测试覆盖（`test_research_safety.py`，19 个非公网地址参数化 + 场景）

- IP 分类拒绝：`127.0.0.1`、`127.5.6.7`、`0.0.0.0`、`10.0.0.1`、
  `172.16/31.x`、`192.168.x`、`169.254.169.254`、`169.254.0.1`、
  CGNAT `100.64.0.1`、`::1`、IPv6 link-local / ULA、
  IPv4-mapped `::ffff:127.0.0.1` / `::ffff:10.0.0.1`、unspecified、
  multicast、reserved；公网 IPv4/IPv6 放行；
- URL 边界：`file://`、`ftp://`、`data:`、`javascript:`、
  `user:pass@` userinfo、无 scheme、空 URL 全部拒绝；
- URL 解析边界：大小写、`:443` 默认端口、fragment、tracking 参数、
  非默认端口、canonical 去重；
- DNS：private 解析拒绝、public+private 混合解析拒绝、DNS 失败拒绝、
  IPv4-mapped DNS 拒绝、`127.0.0.1.nip.io` 类拒绝；
- fetch 层：首跳 private 在**发出任何 HTTP 请求之前**拒绝（handler 未被调用）；
  `public → 127.0.0.1` redirect 第二跳 SSRF 拒绝；
  `public → private hostname` redirect 拒绝；redirect 循环有界终止。

### Fetch / Extraction / Pipeline 覆盖

- 200 HTML / text/plain、404、500、timeout、oversize（bounded read）、
  不支持 content-type（按响应头判断不看扩展名）、redirect 成功（chain 记录）、
  无 cookie / 无 Authorization 头、非法编码不崩溃；
- HTML fixture：正文保留，script/style/nav/footer/cookie banner 不进正文，
  char 上限有效，空页面产出空文本；
- selection：rank 有序、canonical 去重、域名 cap、预算 cap、确定性；
- pipeline：partial success（1/3 成功 → SUCCESS）、全失败 →
  NO_USABLE_EVIDENCE、无结果 → NO_RESULTS、provider 不可用 →
  PROVIDER_UNAVAILABLE（区别于"无证据"）、provider 错误 →
  TECHNICAL_FAILURE、content hash 去重、SSRF 候选安全摘要、空页面
  EMPTY_OR_LOW_QUALITY、预算上限（1 次 search、≤6 结果、≤4 fetch）、
  overall timeout → TIMEOUT、kill switch fail-closed（0 成本）、
  provenance（search_result_url + redirect_chain + final URL）、
  evidence 标记 `origin=web, trust=untrusted`、W1 命名空间；
- Tavily adapter（MockTransport）：结构化映射、5xx/坏 payload →
  unavailable、空 key 构造即拒绝；key 只出现在 Authorization 头。

## 3. Provider 与配置

- 真实 provider：**Tavily**（官方稳定 HTTP API、结构化 JSON、Bearer key、
  max_results 可控）；`ZGLAB_RAG_SEARCH_API_KEY` 仅经环境变量，仓库内
  无任何真实 key（代码 / 测试 / 文档 / 日志均无）；
- `ZGLAB_RAG_WEB_RESEARCH_ENABLED` 默认 `false`（fail-closed，直到 12D
  产品验收）；`build_research_service` 在缺 key 时构造即报错；
- 预算按 2 vCPU / 2 GiB 保守默认（见 `docs/web-research-runtime.md` §11）。

## 4. Remaining Risks（真实遗留）

1. **DNS rebinding 窗口**：validate 与 connect 非原子；带 TLS hostname
   处理的连接 pinning 是后续加固项（文档已明示）；
2. 抽取为启发式确定性规则，真实网页质量参差——低质量页面只影响 evidence
   数量，不影响安全边界；
3. Tavily 生产可用性 / 成本未实测（key 未配置、开关关闭）；12D 前需要
   真实环境 smoke test；
4. `overall_timeout` 为候选间 deadline 检查，不是硬中断——单跳仍受
   fetch_timeout 限制，组合上界 = candidates × fetch_timeout，已被
   30s deadline 收敛；
5. brotli 解压未启用（Accept-Encoding 只声明 gzip/deflate），无压缩炸弹
   放大面，但可能错过少量压缩带宽。

## 5. Phase 状态

```text
Phase 12A complete
Phase 12B complete
Phase 12C not started
Phase 12D not started
```
