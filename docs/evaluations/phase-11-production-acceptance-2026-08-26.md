# Phase 11 — Production Acceptance & Seal（2026-08-26）

## 1. 结论

Phase 11 — Authentication & Access Control 已于 **2026-08-26** 在
`https://ask.zglab.fun` 完成真实生产迁移、HTTPS 浏览器端到端验收、双数据库备份验收与
运维检查，正式封板。

本记录是 `docs/evaluations/phase-11-authentication-acceptance.md` 的生产后继记录：
前者保留 2026-08-25“本地实现完成、生产尚未迁移”的真实历史状态；本文记录下一日实际生产部署后的最终证据。

本次上线的 Phase 11 主代码基线：

```text
7d8f5a0ef6f440a3fc88acbb2922c196aacf8f73
feat: complete phase 11 authentication and access control
```

部署实录与可复用 runbook：`docs/phase-11-production-deployment-2026-08-26.md`。

---

## 2. 生产迁移结果

生产环境最终状态：

```text
Public HTTPS:     https://ask.zglab.fun
Reverse proxy:    Nginx
API:              Uvicorn / FastAPI @ 127.0.0.1:8000
Runtime user:     zglab
Application:      /opt/zglab-rag/app
Environment:      /opt/zglab-rag/.env
Knowledge DB:     /opt/zglab-rag/runtime/knowledge.db
Auth DB:          /opt/zglab-rag/runtime/auth.db
Backups:          /opt/zglab-rag/runtime/backups
Frontend:         /var/www/zglab-assistant
```

正式 `auth.db` 初始化为 schema version 2，文件权限为 `0600`。
首个生产 ADMIN 通过 CLI provision + single-use fragment activation 完成激活；数据库中的密码为
Argon2id hash，activation token 已消费。

---

## 3. 公网匿名态验收

真实公网 HTTPS 请求结果：

| 检查项 | 结果 |
| --- | --- |
| `GET /health` | 200 `ok` |
| `GET /ready` | 200 `ready` |
| `POST /api/v1/ask` | 410 `API_RETIRED` |
| 匿名 `POST /api/v2/ask` | 401 `AUTHENTICATION_REQUIRED` |
| 匿名 `GET /api/v2/auth/me` | 401 `AUTHENTICATION_REQUIRED` |
| `/` | 200，Phase 11 SPA 构建产物已生效 |

因此旧 v1 匿名 LLM 消费入口已退役，v2 未认证请求无法越过 AuthN 边界。

---

## 4. 浏览器认证验收

真实浏览器完成以下检查：

1. activation URL 使用 `/activate#token=...`；页面加载后 fragment 立即从地址栏清除；
2. 用户状态从 `PENDING` 变为 `ACTIVE`；
3. 密码落库为 `$argon2id$...`；
4. 登录成功；
5. Session Cookie 名为 `__Host-zglab_session`；
6. Cookie 属性为 Secure / HttpOnly / SameSite=Lax / Path=/ / host-only；
7. `document.cookie` 无法读取 session；
8. 登录态 `GET /api/v2/auth/me` 返回 200；
9. 缺少 `X-CSRF-Token` 的 authenticated Ask 返回 403 `CSRF_REJECTED`；
10. 正常 Ask 返回 200；
11. authenticated SSE 正常完成；
12. logout 后 Cookie 消失；
13. logout 后 `/api/v2/auth/me` 返回 401，旧 session 立即失效。

因此 Session、Cookie、CSRF、Ask、SSE 与 revoke 链路均在真实生产浏览器中闭环。

---

## 5. Nginx Security Header 验收与修复

首次生产检查发现：server 级已经配置 CSP / nosniff / SAMEORIGIN / Referrer-Policy，
但 `/`、`/api/`、静态资源 location 因自身声明 `add_header`，触发 Nginx 的
**add_header 不继承**规则，导致这些 location 实际响应没有继承 server 级安全头。

生产侧已 hotfix 并重新验证：

- `Content-Security-Policy`：存在；
- `X-Content-Type-Options: nosniff`：存在；
- `X-Frame-Options: SAMEORIGIN`：存在；
- `Referrer-Policy: strict-origin-when-cross-origin`：存在；
- Landing、静态 JS、API 响应均已实际验证。

仓库 `deploy/nginx/ask.zglab.fun.conf` 随后正式修复为自包含模板：
location 只要声明自己的 `add_header`，就同时声明完整安全头，避免未来发布再次覆盖生产修复；
静态资源改用单一显式 `Cache-Control: public, max-age=604800, immutable`，避免 `expires 7d`
与显式 Cache-Control 同时产生重复字段。

生产当前 hotfix 与仓库修复模板在安全语义上等价；下次 Nginx 部署直接以仓库模板为准。

---

## 6. 数据库与备份验收

Phase 11 backup unit 已从单 `knowledge.db` 扩展为：

```text
zglab-rag backup
zglab-rag backup --auth
```

2026-08-26 手动触发真实生产 backup service，两个 ExecStart 均：

```text
status=0/SUCCESS
```

最新备份结果：

- knowledge backup：约 9.5 MiB，权限 `0644`，继承 `knowledge.db`；
- auth backup：约 60 KiB，权限 `0600`，继承 `auth.db`；
- 两个数据库 `PRAGMA integrity_check` 均为 `ok`；
- auth backup 中 schema version 为 2；
- auth backup 中生产 ADMIN 状态为 `ACTIVE`。

backup service 为 `Type=oneshot`，执行后显示 `inactive (dead)` 属于正常状态；
关键判断依据是 ExecStart 的 `status=0/SUCCESS` 与 timer 仍为 active。

---

## 7. 最终 systemd / Nginx 状态

最终生产检查：

```text
zglab-rag-api.service      active
nginx                      active
zglab-rag-backup.timer     enabled + active
zglab-rag-sync.timer       enabled + active
/health                    ok
/ready                     ready
```

backup timer 按既有计划每天运行，sync timer 保持既有每日同步计划。

---

## 8. 封板后的已知限制

以下为已接受限制，不阻塞 Phase 11 封板：

1. Login throttle 为单进程内存状态，API 重启会清零；当前单实例、小规模受控账号场景可接受；
2. `usage` 表暂无定时清理；当前用户规模与写入量很小，可后续作为非编号运维优化；
3. 当前服务器 `.venv/bin/zglab-rag` 曾因历史跨机器复制 `.venv` 留有绝对 shebang，生产运维推荐使用
   `.venv/bin/python -m zglab_rag.cli`，不要依赖 console script；
4. 生产 Server Node 18 不能构建当前 Vite 7；前端发布继续推荐在满足版本要求的构建环境生成 immutable artifact；
5. Nginx 仍存在与其他站点相关的 `protocol options redefined for [::]:443` warning；本次不是 Phase 11 引入，
   不影响 `nginx -t`、reload 或当前站点服务，可作为服务器级独立清理项处理。

不存在已知的 Phase 11 认证旁路、匿名消费入口或生产数据完整性问题。

---

## 9. Phase 11 封板规则

自 2026-08-26 起：

- Phase 11 状态：**COMPLETE / PRODUCTION ACCEPTED / SEALED**；
- 不再向 Phase 11 增加 Web Research、MCP、Agent Planner、Session Context 等新功能；
- 后续若出现认证安全漏洞、生产运维故障或必要兼容性问题，可以作为 Phase 11 maintenance 修复；
- 下一 Product Phase 唯一入口为 **Phase 12 — Agent Capability Foundation & Web Research**；
- Phase 12 及以后必须继续复用 Phase 11 的 AuthN / AuthZ / CSRF / quota / cost boundary，不得绕过。

最终结论：**Phase 11 可以正式封板。**
