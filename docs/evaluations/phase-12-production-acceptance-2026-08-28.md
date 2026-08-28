# Phase 12 — Production Acceptance / Seal

> 日期：2026-08-28 ｜ 生产地址：`https://ask.zglab.fun` ｜ 结论：**ACCEPTED / SEALED**

本记录只陈述实际执行的生产验证；不记录任何 API key、Cookie、CSRF token、
密码、回答正文或用户私有内容。

## 1. 发布基线与可回滚性

- 生产应用代码：`72e4e9c6e681b081d99ab470648fcf5a42f35184`
  （修复英文“current”时新意图的 deterministic Web 路由）；部署归档 SHA-256：
  `a462a2e9769e9028c8ed1a44305af7b2ba405eab0de590055964d70e0e4b6e92`。
- Phase 12 初始生产快照保存在
  `/opt/zglab-rag/rollback/pre-phase12-20260827T011626Z`，包括应用、venv、
  前端、systemd、Nginx 和受限权限的生产配置副本；恢复路径不依赖 Git working tree。
- 生产 `auth.db` 已用正式 CLI 升级并校验为 schema v3；既有账号、会话与数据保留。
- 本次最终原子备份成功：`auth-20260828T025042Z.db`（0600）与
  `knowledge-20260828T025042Z.db`；两份 SQLite `PRAGMA integrity_check=ok`。

## 2. 最终生产配置

- `WEB_RESEARCH_ENABLED=true`；Search provider 为 Tavily，key 仅存在于受限
  生产环境配置中，未写入仓库、日志或本记录。
- Web 独立额度恢复为 **3/分钟、20/日**，research concurrency 为 **1**；普通
  Personal quota 不与 Web bucket 共用。
- `zglab-rag-api.service` 与 Nginx 均为 active，API、backup、sync 的 systemd
  单元/定时器均已 enabled；`/health`、`/ready` 返回 200。

## 3. 真实 HTTPS Web 验收

所有下列 API 调用均通过 `https://ask.zglab.fun`，使用受控、短期 USER 账号；
账号在测试结束后被 disabled，全部会话撤销，激活凭证、Cookie、响应文件和临时
配置均已销毁。

| Gate | 实际结果 |
| --- | --- |
| Provider smoke | Tavily 真实调用：1 次 search、6 条 search result、4 个 fetch candidate、2 条 fetched/evidence；最终带 1 条 citation，研究 status=success。 |
| 显式 Web | `POST /api/v2/ask {mode:web}` 返回 200；source 带 `origin=web`、HTTPS URL 与 domain。 |
| 自动时新路由 | 初次真实测试发现泛化英文 “current” 未被标记；修复并部署 `72e4e9c` 后，`What is the current stable release of Python?` 日志为 `selected_capability=web_research selection_reason=current_information`，且进入独立 Web usage bucket。该次因 citation validation 无足够可用证据返回合法的 `insufficient_evidence`，未伪造来源。 |
| Personal / ambiguity | self-reference 请求明确选择 `personal_knowledge / personal_self_reference`；模糊请求保守选择 `default_personal`。既有 ADMIN 在 Web-off 状态下完成 Personal 回归并由操作者确认。 |
| Web SSE | 真实 SSE 顺序为 `accepted → researching → generating → validating → completed`；`researching` 阶段只发送 `request_id` 与 `stage`，无 URL、正文或未信任页面内容泄露。 |
| 并发 | Web SSE 进行时的第二个 Web 请求返回 `503 SERVICE_BUSY`；拒绝没有增加 Web usage。 |
| 匿名与 CSRF | 匿名 v2 Web 返回 `401 AUTHENTICATION_REQUIRED`；已登录但缺少 CSRF 返回 `403 CSRF_REJECTED`；两者均不计 Web usage。 |
| kill switch | `WEB_RESEARCH_ENABLED=false` 时，显式 Web 返回 `503 CAPABILITY_DISABLED`；auto 时新请求回退 Personal（`web_disabled_fallback_personal`）并保持 Web usage 为 0。恢复开启后健康检查通过。 |
| Provider 故障隔离 | 临时、无效 provider 值下，真实 Web 请求返回安全的 `503 PROVIDER_UNAVAILABLE`；`/health`、`/ready` 仍为 200，随后原配置已原子恢复。 |
| 配额 | 运行时确认读取到 `3/分钟、1/日` 的受控测试配置。首个真实 Web 请求为 200，紧随其后返回 `429 QUOTA_EXCEEDED`；`web_usage` 保持 1，拒绝未消费额度。随后恢复至 3/分钟、20/日。 |

用户浏览器侧确认的 Personal 回归与上述受控 HTTPS API/SSE 验收共同构成生产
验收证据。未将“无自动化浏览器录制”误记为浏览器自动化结果。

## 4. 网络与证据安全门

- `SafeFetcher` 每个 redirect hop 都验证目标并经
  `PinnedResolutionBackend` 只连接已验证的公网 IP；真实 smoke 亦走该生产
  fetch 路径。
- TLS hostname verification / SNI 和 Host header 保持原 hostname；代码与
  生产路径均未使用 `verify=False`。
- Web evidence 是 request-scoped，来源 URL 只能从 provider provenance 进入
  response；无证据/不足证据时不生成来源。
- 公开复核：`/`、`/health`、`/ready` 为 200；旧 `/api/v1/ask` 为
  `410 API_RETIRED`；匿名 `/api/v2/ask` 为 `401 AUTHENTICATION_REQUIRED`。
  CSP、`nosniff`、`SAMEORIGIN`、严格 referrer policy 均已在 HTTPS 响应中确认。

## 5. 本地回归与封板范围

封板前实际执行并通过：

- `uv run pytest -q`（全量执行完成；pytest cache 记录 514 个 node id，
  `lastfailed={}`）；
- `uv run ruff check .`；
- `cd web && npm test -- --run`（5 files / 76 tests passed）；
- `cd web && npm run build`（Vue type-check 与 Vite production build 成功）。

此次封板只完成 Phase 12，不引入 MCP、Agent、Session Context 或新的公开 endpoint。

剩余运维事实：Web 质量仍受公开网页与 citation validation 约束；自动选择是小型
确定性规则，未来若扩展语言/意图，需要新增测试后再改规则。这些不是已知的安全
绕过或未完成的 Phase 12 gate。

```text
Phase 12A ✅  Phase 12B ✅  Phase 12C ✅  Phase 12D ✅
Phase 12 production accepted: YES
Phase 12 status: SEALED
Next product phase: Phase 13（requires separate authorization）
```
