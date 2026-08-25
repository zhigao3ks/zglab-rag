# Authentication & Access Control（Phase 11）

本文档冻结 Phase 11 的认证与访问控制设计。权威 Phase 定义见
`docs/roadmap-v2.md`；API v2 契约细节见 `docs/api-v2.md`。

## 1. Threat Model

系统暴露在公网（`https://ask.zglab.fun`），所有 LLM 与未来 Search / MCP /
Agent 成本由项目所有者个人承担。主要威胁与对策：

| 威胁 | 对策 |
| --- | --- |
| 匿名滥用 LLM 消费 | 全部消费型端点迁移到认证后的 `/api/v2`；v1 退役返回 410 |
| 凭证爆破 / 用户名枚举 | 登录双维度限流（per-IP + per-username）；统一错误响应；`dummy_verify` 均衡时序 |
| 密码库泄露 | Argon2id 哈希；数据库只存 `password_hash` |
| Session 劫持 / 库泄露 | 随机高熵 Session Token；数据库只存 SHA-256；Secure/HttpOnly/SameSite=Lax/host-only Cookie |
| CSRF（Cookie 认证固有） | Origin/Referer 校验 + session-bound CSRF token（`X-CSRF-Token`） |
| Activation / Reset 链接被重放 | 单次使用、过期、可撤销；数据库只存 SHA-256 |
| 账号停用后残留会话 | disable / reset / revoke 立即撤销全部 session，下一请求即失效 |
| 登录后滥用 | per-user 每分钟速率 + 每日配额（429） |
| 成本失控 | `ZGLAB_RAG_LLM_ENABLED=false` kill switch；未来 `WEB_RESEARCH_ENABLED` / `MCP_ENABLED` 沿用同模式 |

明确不在本 Phase 处理：OAuth/OIDC、CAPTCHA、邮件/短信通道、多实例分布式限流。

## 2. Identity Model

角色只有两种，不过度设计 RBAC：

```text
ADMIN   管理员（= 系统所有者），通过服务器 CLI 管理账号
USER    普通用户，可调用认证后的 Assistant
```

与 FastAPI 解耦的身份对象：

```python
AuthenticatedPrincipal(user_id, username, role, session_id)
```

用户状态机：

```text
PENDING ──activation──▶ ACTIVE ──admin disable──▶ DISABLED
                          ▲                          │
                          └─────admin enable─────────┘
```

用户名规范化：`strip().lower()`，字符集 `a-z0-9._-`（首字符为字母/数字，2–32
字符），唯一约束作用在规范化后的值上；`Admin` / `admin` / `ADMIN` 不可能成为
三个账号。

**禁止公开注册**：不存在 `/register` / `/signup`；账号只能由管理员 CLI 创建。

## 3. User Lifecycle

```text
Admin CLI: zglab-rag user create <username>
    ↓ 创建 PENDING 用户 + 单次 Activation Token
CLI 输出一次性 activation URL（仅此一次，不落日志、不重复显示）
    ↓ 管理员通过可信渠道发给用户
用户打开 /activate/<token> 设置密码
    ↓ token 原子消费（UPDATE ... WHERE consumed_at IS NULL）
账号 ACTIVE，可以登录
```

管理员全程不知道用户最终密码。

## 4. Password Security

- 算法：Argon2id（`argon2-cffi` 的 `PasswordHasher` 默认参数，参数内嵌于 hash
  字符串，便于未来升级）；
- 禁止 SHA-256/MD5/明文/可逆加密/自实现算法；
- 策略：长度优先，最短 12、最长 128（上限同时防御超长输入 DoS），无大小写/
  数字/符号组合规则；
- 数据库只存 `password_hash`；修改密码记录 `password_changed_at`。

## 5. Activation Lifecycle

`credential_tokens` 表统一管理 activation 与 reset：

- 生成：`secrets.token_urlsafe(48)` ≈ 384 bit 熵（≥ 256 bit 要求）；
- 存储：仅 SHA-256(token)；明文只存在于一次性 URL；
- 属性：`created_at` / `expires_at`（默认 24h，可配置）/ `consumed_at` /
  `revoked_at` / `purpose`；
- 单次使用：事务内 `UPDATE ... WHERE consumed_at IS NULL`，竞争失败即拒绝；
- 新 token 签发时同 purpose 旧 token 全部 revoke；
- purpose 隔离：`ACTIVATE_ACCOUNT` 与 `RESET_PASSWORD` 永不交叉使用；
  API 层用两个 purpose-pinned endpoint 强制（见第 6 节与 `docs/api-v2.md`）。

**链接传输（Hardening）：fragment transport。** CLI 输出
`https://ask.zglab.fun/activate#token=...`（reset 附加
`&purpose=reset`）。URL fragment 从不发送到服务器，因此 token 不会进入
Nginx access log、应用日志或 Referer；Vue 在 `/activate` 从
`location.hash` 读取后立刻 `history.replaceState` 清除，只通过 POST body
提交。token 不出现在 path、query、服务端日志或浏览器历史中。

## 6. Password Reset

无公开 forgot-password 流程；用户联系管理员：

```text
zglab-rag user reset-password <username>
    ↓ 同一事务内：撤销全部 session + credential 置为 RESET_REQUIRED
    ↓ 生成单次 RESET_PASSWORD token（PENDING 用户则重发 activation token）
CLI 输出一次性 reset URL（fragment：#token=...&purpose=reset）
    ↓ 用户打开 /activate 页面设置新密码（POST /api/v2/auth/reset-password）
旧密码失效（签发时即失效），旧 session 全部失效
```

**旧密码立即失效（Hardening）**：`users.credential_status` 在签发 reset
token 的同一事务内被置为 `RESET_REQUIRED`；登录流程在密码校验后检查该
状态，因此旧密码在 token 被消费前就已失去登录能力。只有成功消费 reset
token（设置新密码）才会回到 `VALID`。这是 fail-closed 设计：token 过期未
消费时账号保持无法登录，直到管理员重新签发。

**endpoint purpose 强校验（Hardening）**：
`POST /api/v2/auth/activate` 只消费 `ACTIVATE_ACCOUNT`，
`POST /api/v2/auth/reset-password` 只消费 `RESET_PASSWORD`；不存在根据
token 内容自动分派凭据操作的公开 endpoint，cross-purpose 提交一律
400 拒绝且不消费 token。

## 7. Session Model

Server-side Session（明确不采用 JWT + localStorage）：

- 登录成功生成随机 Session Token（`secrets.token_urlsafe(48)`）；
- 数据库 `sessions` 表只存 SHA-256(token)，另存 `csrf_secret`、
  `created_at`、`last_seen_at`、`idle_expires_at`、`absolute_expires_at`、
  `revoked_at`、有限 client hint（截断的 IP 提示，不存完整 UA，也不作为
  认证条件）；
- 双超时：idle 7 天（每次成功请求滑动续期）、absolute 30 天（均可配置）；
- 每次成功解析校验用户仍为 ACTIVE——管理员 disable 立即生效；
- 会话撤销入口：logout / admin `revoke-sessions` / disable / password reset /
  change-password（撤销其他会话，保留当前会话）。

## 8. Cookie Security

```text
名称:   __Host-zglab_session（可配置）
Secure: 生产 true（__Host- 前缀要求）
HttpOnly: true（JS 永不可见 session token）
SameSite: Lax
Path:   /
Domain: 不设置（host-only 语义）
```

本地纯 HTTP 开发时浏览器不接受 Secure/__Host- cookie，可临时配置
`ZGLAB_RAG_AUTH_COOKIE_NAME=zglab_session_dev` +
`ZGLAB_RAG_AUTH_COOKIE_SECURE=false`；生产必须保持默认。

**配置 fail-fast（Hardening）**：Settings 校验器拒绝
`__Host-*` + `Secure=false` 的弱组合（浏览器本来就会拒收这种 cookie，
静默失败等于把生产推向坑里）；生产配置因此不可能误用弱 cookie。

Session token 不进入 localStorage / sessionStorage / Vue 持久化状态 / 日志 /
URL / 任何响应体。

## 9. CSRF

Cookie 会话必须叠加两层防护，不只依赖 SameSite=Lax：

1. **Origin validation**：所有 state-changing POST（login / logout / activate /
   change-password / ask / ask/stream）必须携带与
   `ZGLAB_RAG_AUTH_ALLOWED_ORIGINS`（默认 `auth_public_base_url`）匹配的
   `Origin`（或 `Referer`）；两者皆缺直接拒绝；
2. **Session-bound CSRF token**：登录/`me` 响应体下发
   `csrf_token = HMAC-SHA256(session.csrf_secret, "csrf:<session_id>")`；
   认证后的 state-changing 请求必须携带 `X-CSRF-Token`，常量时间比较。

CSRF token 由 SPA 保存在内存中，不落 localStorage。SSE 与普通 ask 走同一个
安全门（`_v2_ask_preflight` + `_v2_security_gate`），SSE 不构成旁路。

## 10. Login Abuse Protection

独立于 ask IP limiter 的进程内登录限流（`LoginThrottle`）：

- per IP：10 次 / 600 秒（默认，可配置）；
- per username：5 次 / 900 秒（默认，可配置）；
- 尝试在校验前记账，错误密码风暴也被限流；
- 超限返回 429 `RATE_LIMITED` + `Retry-After`。

反枚举：

- 用户不存在 / 密码错误 / 账号停用 / 未激活全部返回同一
  401 `INVALID_CREDENTIALS` 与同一文案；内部审计日志才区分原因；
- 用户不存在时执行 `dummy_verify` 均衡 Argon2 时序；
- 不实现 CAPTCHA、不接第三方验证。

## 11. Authorization

当前规则极简：**ACTIVE 状态的已认证用户**即可调用 Assistant。

- 服务端强制（`SessionService.resolve_session` 校验状态），default-deny；
- 不依赖 Vue route guard、隐藏按钮、客户端 role 声明或 Prompt/LLM；
- `require_authenticated_role` 为未来 ADMIN/OWNER 能力预留，但本 Phase 不
  开放任何管理员 Web 能力；
- 登录 ≠ private knowledge 开放：检索仍强制 `visibility == public`。

## 12. Quota

`auth.db` 的 `usage` 表按 `(user_id, day, minute)` 记账，跨重启有效：

- `auth_user_requests_per_minute`（默认 10）；
- `auth_user_requests_per_day`（默认 100）；
- 超额返回 429 `QUOTA_EXCEEDED` + `Retry-After`，并写 `quota_exceeded`
  审计事件。

**记账策略与原子性（Hardening）**：

- 只有真正进入 cost-bearing workflow 的请求才计数。记账发生在
  AuthN/AuthZ/CSRF、kill switch、问题长度校验与并发槽获取之后；
- 认证失败、CSRF 失败、SERVICE_BUSY、超额本身均不消费配额；
- check + increment 在单个 `BEGIN IMMEDIATE` 事务中完成（先增后查，
  超限回滚），消除并发 check-then-increment 竞态；
- 已记账但生成任务未能提交（优雅关闭窗口竞态）时通过 `refund` 退回。

domain 层为未来能力预留概念（不在本 Phase 实现）：`web_research_allowed` /
`mcp_allowed` / `agent_allowed`。

## 13. Audit

`auth.db` 的 `audit_events` 表 + 结构化 JSON 日志（journald），事件覆盖：

```text
login_success / login_failure / logout
account_created / account_activated
password_changed / password_reset_requested_by_admin
session_revoked / account_disabled / account_enabled
quota_exceeded
```

记录字段仅限：时间戳、事件、`request_id`、`user_id`、结果、安全 client hint
（截断 IP）。**禁止**记录：密码、password_hash、activation/reset token、
session token、CSRF secret、LLM API key、完整问题内容。测试断言数据库 dump
中不出现明文密码与 session token。

## 14. Database

独立 `auth.db`（`ZGLAB_RAG_AUTH_DATABASE_PATH`，生产为
`/opt/zglab-rag/runtime/auth.db`），绝不写入 `knowledge.db`：

```text
schema_metadata   schema_version=2（显式初始化、版本校验、fail-fast、v1→v2 迁移）
users             username UNIQUE、password_hash、role、status、credential_status、时间线
sessions          session_hash UNIQUE、csrf_secret、双超时、revoked_at
credential_tokens token_hash UNIQUE、purpose、expires/consumed/revoked
usage             (user_id, day, minute) 消费计数
audit_events      审计事件
```

- `users.credential_status`（`VALID` / `RESET_REQUIRED`）：reset 签发时
  立即废弃旧密码（schema v2 新增，v1 库自动迁移）；
- WAL 模式；短连接按操作开关；
- 非本系统数据库、版本不符、无法打开一律抛 `AuthDatabaseError`，不静默降级；
- 新建 auth.db 文件权限硬化为 `0600`（hash 库不得 group/world 可读）；
- `*.db` 已在 `.gitignore`，auth.db 不进入 Git。

未来迁移：schema 版本升级时新增显式 migration 函数（参照 knowledge.db 的
`migrate_v1_to_v2` 模式）。

## 15. Backup / Restore

`zglab-rag backup --auth` 复用与 knowledge.db 相同的原子 SQLite backup：
临时文件写入 → fsync → 原子 rename，文件名前缀 `auth-`，与 `knowledge-`
分别独立保留最近 7 份。`zglab-rag-backup.service` 每天同时备份两个库。

**权限（Hardening）**：备份文件继承源数据库的权限位（而不是 umask），
因此 0600 的 auth.db 备份同样是 0600，password/session/token hash 的备份
不会获得比 auth.db 更宽松的权限。

恢复步骤（与 knowledge.db 一致）：停止 API，将核验过的备份复制为
`runtime/auth.db`，确认属主 `zglab:zglab` 与 0600 权限，启动 API；恢复前
先移动原文件保留。

## 16. Admin CLI

```bash
zglab-rag auth init                      # 显式初始化 auth.db
zglab-rag user create <name> [--role ADMIN|USER]   # 输出一次性 activation URL
zglab-rag user list
zglab-rag user show <name>               # 不含任何秘密，不重印 token
zglab-rag user disable <name>            # 立即撤销全部 session
zglab-rag user enable <name>
zglab-rag user reset-password <name>     # 撤销 session + 输出一次性 reset URL
zglab-rag user revoke-sessions <name>
zglab-rag backup --auth
```

Bootstrap 第一个管理员：`zglab-rag user create owner --role ADMIN`，随后用
activation URL 设置密码。不存在 `admin/admin` 自动创建、默认密码或 `.env`
明文管理员密码。

## 17. Production Migration

见 `docs/evaluations/phase-11-authentication-acceptance.md` 的迁移步骤章节。
要点：先建 auth.db 与管理员账号 → 部署新后端与前端 → 验证登录与 v2 →
置 `ZGLAB_RAG_API_V1_RETIRED=true` 重启，v1 返回 410。

## 18. Known Limitations

1. 登录限流与并发守卫为进程内状态：单实例部署成立，多实例需重新设计；
2. `usage` 计数无自动过期清理任务（数据量极小，预留 `prune_old` 接口）；
3. 本地 HTTP 开发需临时关闭 Secure cookie（见第 8 节），生产不受影响；
4. v1 退役依赖生产迁移时显式置 `ZGLAB_RAG_API_V1_RETIRED=true`；配置未翻转
   前 v1 仍按 Phase 9 冻结契约工作（默认 false 保证 Phase 0–10 回归）；
5. 无邮件通道，activation/reset URL 依赖管理员人工经可信渠道下发；
6. CSP 采用保守配置（style-src 保留 'unsafe-inline'），未启用 report-only
   观察期之外的更严格策略。
