# Web Research Runtime（Phase 12B）

本文档定义 Phase 12B 落地的 Web Research Core：一条独立、bounded、
deterministic、可测试的管线，把问题转换为 **untrusted ExternalEvidence[]**。
它不生成回答、不接 `/api/v2/ask`、不写 `knowledge.db`；与 Grounded
Generation 的组合属于 Phase 12C。

## 1. Research Pipeline

```text
Question / Research Query
      ↓ deterministic normalization（无 LLM）
SearchProvider.search()          # 每次 research 恰好 1 次调用
      ↓
SearchResult[]（≤ max_search_results）
      ↓ deterministic Candidate Selection（rank + dedupe + domain cap + URL 安全）
Candidate[]（≤ max_fetch_candidates）
      ↓ Safe Fetch（每跳 SSRF 重验）
Raw Page（bounded bytes）
      ↓ deterministic Extraction（HTML → text，无 LLM）
Normalized Text（≤ max_extracted_chars）
      ↓ dedupe（canonical URL + content hash）
ExternalEvidence[]（W1, W2, ...）
```

入口：`ResearchService.research(query, request_id) -> ResearchResult`。
未来 Agent Runtime 通过 `WebResearchSkill.execute(CapabilityRequest, ...)`
调用同一条管线。

## 2. SearchProvider Contract

```python
class SearchProvider(Protocol):
    name: str
    def search(self, query: str, *, limit: int) -> list[SearchResult]: ...
```

- vendor 响应在 adapter 内部立即映射为 `SearchResult`，provider JSON 不外泄；
- 12B 只接入一个真实 provider（Tavily）+ `FakeSearchProvider`（确定性测试）；
- 换一个 provider = 换一个 adapter，上层零改动。

## 3. Search Result Model

`SearchResult`：`title / url / snippet / rank / provider`（`published_at`
预留，provider 不稳定返回时不使用）。不包含 provider 原始 JSON。

## 4. Candidate Selection

确定性、可测试、bounded，**不由 LLM 决定抓哪些 URL**：

1. 按 provider rank 升序；
2. `canonicalize_url` 失败的（scheme 不允许 / userinfo / 结构非法）丢弃；
3. canonical URL 去重；
4. 每域名最多 `max_candidates_per_domain`（默认 2）条；
5. 取满 `max_fetch_candidates`（默认 4）即停。

## 5. URL Canonicalization

`scheme`/`host` 小写、去 fragment、去默认端口（80/443）、去常见 tracking
参数（`utm_*`、`fbclid`、`gclid`）；非默认端口保留。canonical 形式只用于
去重与比较；**原始 SearchResult URL 始终保留**用于 provenance。不做任何
危险的 URL 重写。

## 6. SSRF Threat Model

Web Research 是 authenticated 用户可间接触发的服务器端出站请求；
**认证不能替代 SSRF 防护**。拒绝清单（fail-closed，宁错杀）：

| 类别 | 处理 |
| --- | --- |
| scheme | 只允许 `http`/`https`（生产建议 https-only）；`file://`、`ftp://`、`data:`、`javascript:` 等一律拒绝 |
| userinfo | `https://user:pass@host/` 一律拒绝 |
| loopback | `127.0.0.0/8`、`::1` 拒绝 |
| unspecified | `0.0.0.0`、`::` 拒绝 |
| private IPv4 | RFC1918（10/8、172.16/12、192.168/16）拒绝 |
| CGNAT | `100.64.0.0/10` 显式拒绝 |
| link-local / metadata | `169.254.0.0/16`（含 `169.254.169.254`）、IPv6 link-local 拒绝 |
| IPv6 private | ULA（`fd00::/8`）等拒绝 |
| IPv4-mapped IPv6 | `::ffff:a.b.c.d` 解包后按 IPv4 分类 |
| multicast / reserved | 拒绝 |

分类基于 `ipaddress` 的确定性谓词 + 显式网段，不是字符串黑名单。

## 7. DNS Safety

不只检查 URL 字符串：`hostname → DNS resolve → 逐个 IP 分类`。
**任一**解析地址非公网即整体拒绝（public+private 混合解析视为危险信号）。
DNS 失败同样拒绝。resolver 可注入（测试使用 fake，CI 不触公网）。

残留风险（明示）：validate 与 connect 非原子，极端 DNS rebinding 仍有
时间窗；带 TLS hostname 处理的 IP 连接 pinning 是后续加固项。

## 8. Redirect Policy

httpx `follow_redirects=False`，手工逐跳控制：

```text
Location → urljoin → 完整 URL + DNS 安全重验 → 才允许下一跳
```

`public → 127.0.0.1` 的 redirect 在第二跳被 SSRF 拒绝；跳数超过
`max_redirects`（默认 3）安全失败（TOO_MANY_REDIRECTS）。

## 9. Safe Fetch

- 成熟 HTTP client：`httpx`（同步，匹配现有 sync + thread executor 模型）；
- connect + read 超时（`fetch_timeout_seconds`，默认 8s）；
- **不携带**任何 cookie jar / 用户 session / Authorization / 内部 API key；
- User-Agent 明示身份；
- content-type 允许列表见第 10 节；
- bounded streaming read：按**解压后**字节计，超过 `max_response_bytes`
  立即中止（压缩炸弹无法绕过）。

## 10. Content-Type Policy

只接受响应头（不看扩展名）为：`text/html`、`text/plain`、
`application/xhtml+xml`。PDF/DOCX/图片/二进制等 12B 不处理（后续可单独
扩展）。不支持的类型是候选级失败，管线继续其余候选。

## 11. Size / Timeout Budget（2 vCPU / 2 GiB 保守默认）

| 项 | 默认 |
| --- | --- |
| search results | 6 |
| fetch candidates | 4（每域名 ≤2） |
| redirects | 3 |
| per-fetch timeout | 8s |
| overall research timeout | 30s |
| response size | 1.5 MiB（解压后） |
| extracted chars | 8 000 |
| search calls / request | **恰好 1** |

全部经 `Settings` 可调（`ZGLAB_RAG_RESEARCH_*`）。

## 12. HTML Extraction

BeautifulSoup 确定性抽取（无 LLM 总结）：

- 删除 `script / style / noscript / template / nav / footer / header /
  aside / form / iframe / svg / canvas / video / audio / button`、
  `hidden` 元素、`role=navigation|banner`、`aria-hidden=true`、id/class
  含 cookie/consent/gdpr 等 boilerplate；
- 主内容优先 `<article>` → `<main>` → `<body>`；取
  h1–h6/p/li/blockquote/pre/td 文本，空白规整，字符上限截断；
- `text/plain` 直接规整。

## 13. ExternalEvidence

```text
evidence_id     W1/W2/...（request-stable；与 E1/E2 citation 命名空间解耦，12C 统一）
origin          WEB
trust           untrusted
title / content / snippet / domain
url             final URL（来自真实 search 结果或其合法 redirect）
canonical_url
search_rank / retrieved_at
search_result_url + redirect_chain   ← provenance
```

**URL provenance 硬性要求**：证据 URL 只能来自真实 SearchProvider 返回
或其经逐跳验证的 redirect 链；LLM / extractor / 用户文本不得生成 citation
URL。不复用 Personal Knowledge 的 `chunk_id / file_path / heading_path`。

## 14. Prompt Injection Boundary

网页内容是 **UNTRUSTED EXTERNAL DATA**。页面中的“忽略之前指令”“调用工具”
“泄露系统提示词”等文本全部只是 Evidence Text，不是 system/developer/tool
instruction。12B 尚未把内容送给 LLM；12C 接入时必须保持 data boundary。
本阶段只以 `trust="untrusted"` 与文档语义冻结该边界，不建复杂 trust 框架。

## 15. Failure Model

| 状态 | 语义 |
| --- | --- |
| `SUCCESS` | ≥1 条可用证据（允许 partial success：3 候选 1 成功即 SUCCESS） |
| `NO_RESULTS` | search 无结果 / 空查询 |
| `NO_USABLE_EVIDENCE` | 有候选但全部失败或低质量（业务结果，非故障） |
| `PROVIDER_UNAVAILABLE` | search 后端不可用（5xx / 网络 / 拒绝） |
| `TIMEOUT` | 总体预算耗尽且无证据 |
| `TECHNICAL_FAILURE` | 其他基础设施错误 |
| `POLICY_DISABLED` | kill switch 关闭，未花任何成本 |

候选级失败原因（安全摘要，不含 key / 内部异常 / stack trace）：
`ssrf_rejected / unsafe_url / too_many_redirects /
unsupported_content_type / http_error / timeout / oversize /
empty_or_low_quality / fetch_error`。

技术故障**绝不能**被解读为“知识不存在”，也不得触发 LLM 自由补答。

## 16. Logging

只记录：`request_id / provider / 计数（search_results、candidates、
fetched、evidence）/ status / 命中域名 / elapsed_ms`。
不记录：Search API Key、session token、CSRF、cookie、完整网页正文、
完整用户 question。

## 17. Configuration

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ZGLAB_RAG_WEB_RESEARCH_ENABLED` | `false` | kill switch，fail-closed |
| `ZGLAB_RAG_SEARCH_PROVIDER` | `tavily` | provider 选择 |
| `ZGLAB_RAG_SEARCH_API_KEY` | 空 | 只经环境变量；缺失时构造即报错 |
| `ZGLAB_RAG_RESEARCH_*` | 见第 11 节 | 预算上限 |

## 18. Known Limitations

1. DNS rebinding 窗口未用连接 pinning 完全封死（见第 7 节残留风险）；
2. gzip/brotli 之外编码、HTTP/2 细节依赖 httpx 默认行为；
3. 抽取为启发式确定性规则，非 Readability 级精度（12B 明确接受）；
4. `CapabilityRegistry` 暂未注册 `web_research`：registry 存在 ≠ API 可
   调用；12B 不暴露任何 HTTP research endpoint；
5. 单一 search query（无 LLM query rewriting / multi-query）。

## 19. 12C Integration Boundary

12C 才负责：`ExternalEvidence → ContextBuilder → Grounded Generation →
Citation Validation`，统一 E/W citation 命名空间，authenticated API/SSE
集成与 researching 阶段事件。12B 的终点就是 `ExternalEvidence[]`。
