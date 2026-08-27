# Web Research 产品接入（Phase 12D）

> 状态：12D 本地实现与验收完成；**生产尚未开启**（`WEB_RESEARCH_ENABLED=false`，
> 真实 provider smoke 与生产验收是独立前置步骤）。本文描述 Personal/Web 两项
> 产品能力如何安全接入既有 Authenticated API v2。

## 1. 产品调用链

```text
Authenticated User
    → POST /api/v2/ask(/stream) {question, mode?}
    → Origin → AuthN → AuthZ → CSRF → LLM kill switch → question controls
    → Capability Selection（deterministic，非 LLM）
    → ┌ personal: 全局并发 → 个人 quota → PersonalKnowledgeSkill
      └ web:      全局并发 + research 并发(=1) → web quota → WebResearchSkill
    → Grounded Answer + Validated Sources
```

选择发生在安全边界之后、并发与 quota 之前：被拒绝的请求不消耗任何
guard 槽位或 quota。

## 2. Capability Selection Policy（非 LLM）

第一版是小的、可审计的确定性策略（`capabilities/selection.py`），不是
Agent Router：

| 规则（按优先级） | 结果 | reason_code |
| --- | --- | --- |
| `mode=personal` | PERSONAL | `explicit_personal` |
| `mode=web` | WEB | `explicit_web` |
| 自我指涉（我/本人/黄志高/简历/履历…） | PERSONAL | `personal_self_reference` |
| 时新意图（最新/最近/今天/当前/目前/latest…） | WEB | `current_information` |
| 其余与无法判断 | PERSONAL | `default_personal` |
| auto 命中 WEB 但 kill switch 关闭 | PERSONAL（静默降级） | `web_disabled_fallback_personal` |

关键性质：

- 自我指涉**优先于**时新意图（Personal Facts Integrity：web 结果永不自动
  成为用户本人事实）；
- 模糊问题一律 PERSONAL——ambiguity 不产生 Search API 成本；
- 显式 `mode=web` 在 kill switch 关闭时不静默降级，返回
  `CAPABILITY_DISABLED`（503）；
- 选择只记录单个 reason code，没有 reasoning chain；
- **不存在** Personal insufficient → 自动 Web 的 fallback loop。

## 3. API 契约（additive）

请求：

```json
{ "question": "...", "mode": "auto | personal | web" }   // mode 默认 auto
```

- 服务端验证 mode（非法值 422）；不接受任意 capability id；
- 旧客户端只传 `question` 完全兼容。

响应 source（additive，不删改 Phase 9 字段）：

```json
{ "id": "E1", "title": "...", "section": [], "source_path": "...",
  "origin": "personal | web", "url": "https://... | null", "domain": "... | null" }
```

- web source 的 `url/domain` 只能来自 Phase 12B provenance；
- 不暴露 redirect_chain / raw search response / provider metadata。

错误码新增：`CAPABILITY_DISABLED`（kill switch，503）、
`CAPABILITY_DENIED`（权限策略，403）。

## 4. SSE

```text
Personal: accepted → retrieving → generating → validating → completed
Web:      accepted → researching → generating → validating → completed
```

- `researching` 是单一合并事件（search/fetch/extract 不细分）；只含
  `{request_id, stage}`——无 query、无 URL 列表、无 provider 响应、无正文；
- Personal 路径不发 `researching`；公网契约保持小而稳定；
- 流前拒绝（kill switch / 权限 / quota）仍是普通 JSON，不是 SSE。

## 5. 成本与安全边界

| 边界 | 实现 |
| --- | --- |
| Quota | 独立 `web_usage` 表（auth schema v3 迁移），分钟 + 天双限制；web 请求不消耗个人 bucket，反之亦然 |
| 记账语义 | 只在真正进入 WebResearchSkill 前记账；AuthN/CSRF/SERVICE_BUSY/选择 personal 均不消耗 web quota；提交失败 refund |
| 权限 | 服务端策略：kill switch + 可选 `web_research_admin_only`；前端按钮不代表授权 |
| 并发 | 全局 guard 之上叠加 research guard（默认 1）：单实例不同时下载网页/调 Search |
| Kill switch | `WEB_RESEARCH_ENABLED=false` 时 Personal 完全正常；回滚只需翻回该开关 |
| Bounded | 12B 预算（search=1、fetch≤N、redirect≤3、字节/时长上限）原样生效 |

## 6. DNS Rebinding（生产阻断项，已解决）

12B 遗留的 TOCTOU 窗口（DNS 验证 → httpx 连接时二次解析）由
**pinned resolution** 关闭（`research/pinned_transport.py`）：

- 每跳 DNS 验证后把 `host → validated IPs` 固定进 `PinnedHosts`；
- httpcore network backend 的 `connect_tcp` 只连接已验证（且连接前二次
  复核仍为安全公网）的 IP；未 pin 的 host 在建立 socket 前拒绝；
- TLS 验证 / SNI 仍用原始 hostname（httpcore 在 `start_tls` 使用 origin
  host），Host header 不变，**没有** `verify=False`；
- redirect 每跳重新验证并重新 pin。

离线测试（`tests/test_research_rebinding.py`，真实 httpx/httpcore +
脚本化 fake backend）锁定：rebinding 攻击无法触达内网地址；SNI 保持
hostname；redirect 目标逐跳重验。

## 7. 健康检查

Web Research 是 optional capability：Tavily 不可用不导致 `/ready=false`
（Personal RAG / Auth / 核心 generation 正常即 ready）。

## 8. 生产迁移 Runbook

```text
1. 部署 12D 代码，WEB_RESEARCH_ENABLED=false（本次交付状态）
2. Personal RAG 回归（匿名 v1 已退役；v2 personal 正常）
3. 配置 SEARCH_API_KEY（仅环境变量/systemd，不入 Git/日志）
4. Real provider smoke（1–3 次，小预算；记录结果）
5. SSRF/rebinding gate 复核（offline 测试 + 生产配置检查）
6. WEB_RESEARCH_ENABLED=true，重启服务
7. Authenticated Web 产品验收（浏览器端到端 + quota/kill switch 演练）
```

第一次部署绝不直接 `WEB_RESEARCH_ENABLED=true`。

## 9. Rollback

```text
WEB_RESEARCH_ENABLED=false → 重启 → Personal Knowledge Assistant 完全可用
```

单开关回滚，无需代码回退；auto 选择自动降级 personal，显式 web 返回
`CAPABILITY_DISABLED`。
