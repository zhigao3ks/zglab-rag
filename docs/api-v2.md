# Public API v2（Phase 11 Authenticated API）

`/api/v1` 是 Phase 9 的历史冻结契约，定义保留在 `docs/public-api.md`，不被
本文档改写。Phase 11 引入认证后的 `/api/v2`，所有消费型能力迁移到 v2。

## 1. Auth Endpoints

| 方法 | 路径 | 认证 | CSRF/Origin | 说明 |
| --- | --- | --- | --- | --- |
| POST | `/api/v2/auth/login` | 匿名 | Origin | 用户名 + 密码登录，建立 server-side session |
| POST | `/api/v2/auth/logout` | Session | Origin + CSRF（有效会话时） | 立即撤销会话并清除 cookie，幂等 |
| GET | `/api/v2/auth/me` | Session | 无（安全方法） | 恢复前端登录态，返回用户与 CSRF token |
| POST | `/api/v2/auth/activate` | 匿名 | Origin | 只消费 `ACTIVATE_ACCOUNT` token，设置首个密码 |
| POST | `/api/v2/auth/reset-password` | 匿名 | Origin | 只消费 `RESET_PASSWORD` token，设置新密码 |
| POST | `/api/v2/auth/change-password` | Session | Origin + CSRF | 修改密码；撤销其他会话 |

### POST /api/v2/auth/login

请求：

```json
{ "username": "alice", "password": "..." }
```

成功 `200`：`Set-Cookie: __Host-zglab_session=...; Secure; HttpOnly;
SameSite=Lax; Path=/`（host-only），响应体：

```json
{
  "request_id": "…",
  "user": { "username": "alice", "role": "USER" },
  "csrf_token": "…"
}
```

失败统一为 `401 INVALID_CREDENTIALS`（用户不存在 / 密码错误 / 账号停用 /
未激活不区分）；限流时 `429 RATE_LIMITED` + `Retry-After`；Origin 不合法时
`403 CSRF_REJECTED`。

### GET /api/v2/auth/me

无有效会话：`401 AUTHENTICATION_REQUIRED`。有效时返回与 login 相同结构的
`AuthSessionResponse`（每次解析会滑动续期 idle 超时）。

### POST /api/v2/auth/activate 与 POST /api/v2/auth/reset-password

两个 endpoint 请求体相同：

```json
{ "token": "<一次性 token>", "password": "..." }
```

**Purpose 强校验（Hardening）**：两个 endpoint 各自只接受一种 token
purpose，不存在任何 public endpoint 根据 token 内容自动分派凭证操作：

- `POST /api/v2/auth/activate` 只消费 `ACTIVATE_ACCOUNT`，成功 `200
  {"result": "account_activated"}`；
- `POST /api/v2/auth/reset-password` 只消费 `RESET_PASSWORD`，成功 `200
  {"result": "password_updated"}`（旧会话与旧密码已在签发时失效）；
- 跨 purpose 提交（如拿 reset token 调 activate）按无效 token 拒绝。

token 无效 / 已用 / 过期 / 密码不合策略：`400 INVALID_REQUEST`。

**Token 传输（Hardening）**：一次性 token 只通过 URL **fragment** 下发
（`/activate#token=...`，reset 附加 `&purpose=reset`），由前端从
`location.hash` 读取后立即用 `history.replaceState` 清除，仅以 POST body
提交；token 从不进入服务端 URL、access log 或 Referer。

### POST /api/v2/auth/change-password

请求：`{ "current_password": "...", "new_password": "..." }`，必须携带
`X-CSRF-Token`。成功 `200 {"result": "password_changed"}`；当前密码错误
`401 INVALID_CREDENTIALS`；新密码不合策略 `400 INVALID_REQUEST`。

## 2. Ask Endpoints

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v2/ask` | 认证问答（同步返回完整回答） |
| POST | `/api/v2/ask/stream` | 认证问答（status SSE） |

请求体与 v1 相同：`{ "question": "..." }`（extra 字段拒绝）。成功响应 /
SSE 事件契约与 Phase 9A/9B 完全一致（`answered` / `insufficient_evidence`、
`accepted…completed` 阶段事件）；下游检索仍强制 public-only。

两者共用同一个安全门，顺序为（Hardening：安全边界先于能力策略）：

```text
Request Validation（schema / body size）
  → Origin validation
  → Authentication（session cookie → principal）
  → Authorization（仅 ACTIVE 账号）
  → CSRF（X-CSRF-Token）
  → Capability policy（LLM_ENABLED kill switch）
  → Question length
  → Concurrency Guard
  → Quota（per-user per-minute / per-day，原子记账）
  → GroundedAnswerService
```

**Gate precedence（Hardening）**：匿名调用无论 kill switch 状态如何，永远
先得到 `401 AUTHENTICATION_REQUIRED`；capability enabled/disabled 状态
不向未认证方泄露。

pre-stream 拒绝一律为普通 JSON 错误（不开启事件流），因此 SSE 不存在认证
旁路。

## 3. Authentication Behavior

- 凭证：`__Host-zglab_session` HttpOnly Cookie；浏览器自动携带
  （same-origin）；
- 服务端每次请求解析：SHA-256 查 `sessions` → 校验 revoked / idle /
  absolute 超时 → 校验用户仍 ACTIVE → 滑动续期；
- 未认证访问受保护端点：`401 AUTHENTICATION_REQUIRED`；
- logout / admin revoke / disable / password reset 之后，旧 cookie 立即失效。

## 4. Cookie Semantics

```text
__Host-zglab_session=<随机 token>; Secure; HttpOnly; SameSite=Lax; Path=/;
Max-Age=<absolute timeout>
```

- 不设置 `Domain`（host-only）；
- 数据库只存 token 的 SHA-256；
- `ZGLAB_RAG_AUTH_COOKIE_SECURE=false` 仅供本地 HTTP 开发（需同时改 cookie
  名称，见 `docs/authentication.md`）；
- **配置 fail-fast（Hardening）**：Settings 校验器直接拒绝 `__Host-*` +
  `Secure=false` 的弱组合，生产配置不可能误用弱 cookie。

## 5. CSRF

所有 state-changing POST 先做 Origin/Referer 校验；认证后的 state-changing
请求还必须携带 `X-CSRF-Token`（值来自 login / me 响应，保存在前端内存）。
校验失败：`403 CSRF_REJECTED`。logout 对已失效会话不要求 CSRF（幂等清
cookie）。

## 6. Error Model

沿用 Phase 9 信封：

```json
{ "request_id": "…", "error": { "code": "…", "message": "…" } }
```

Phase 11 新增安全错误码：

| code | 典型状态 | 含义 |
| --- | --- | --- |
| `AUTHENTICATION_REQUIRED` | 401 | 需要登录 |
| `INVALID_CREDENTIALS` | 401 | 登录或当前密码校验失败（统一文案） |
| `ACCOUNT_UNAVAILABLE` | 403 | 账号不可用（不泄露具体状态） |
| `CSRF_REJECTED` | 403 | Origin 或 CSRF token 校验失败 |
| `QUOTA_EXCEEDED` | 429 | 用户级速率/日配额超限（带 `Retry-After`） |
| `SERVICE_DISABLED` | 503 | LLM kill switch 生效 |
| `API_RETIRED` | 410 | v1 已退役 |

登录错误不区分 `USER_NOT_FOUND` / `WRONG_PASSWORD` / `DISABLED_ACCOUNT`；
这些区分只存在于内部审计日志。

## 7. SSE Authentication

- `/api/v2/ask/stream` 与普通 ask 走同一安全门；
- 认证/CSRF/配额失败发生在流开启之前，返回 JSON 错误；
- 流内事件契约不变：阶段事件仅含 `request_id` + `stage`，最终答案只在
  `completed` 出现一次。

## 8. v1 Retirement

- `ZGLAB_RAG_API_V1_RETIRED=false`（默认）：v1 保持 Phase 9 冻结行为，
  用于本地回归；
- `ZGLAB_RAG_API_V1_RETIRED=true`（生产迁移后置位）：
  `POST /api/v1/ask` 与 `POST /api/v1/ask/stream` 返回
  `410 API_RETIRED`，不再触达 `GroundedAnswerService`；
- **生产 fail-closed（Hardening）**：`env=production` 的进程启动时若未显式
  置位 `api_v1_retired`，`validate_production_security_settings` 直接抛错
  拒绝启动——漏配环境变量不可能让 v1 继续匿名消费 LLM；
- `docs/public-api.md` 继续作为 Phase 9 v1 冻结的历史文档，不改写。

## 9. CORS / Origin Policy

- 认证完全基于同源 Cookie；CORS 保持 Phase 9 配置
  （`allow_credentials=False`），不做跨域凭证；
- state-changing 请求的 Origin 必须属于
  `ZGLAB_RAG_AUTH_ALLOWED_ORIGINS`（默认取 `auth_public_base_url`）；
- 生产设置为 `["https://ask.zglab.fun"]`。

## 10. Quota Behavior

- 每用户每分钟 `auth_user_requests_per_minute`（默认 10）次；
- 每用户每日 `auth_user_requests_per_day`（默认 100）次；
- 匿名请求在认证阶段即被 401 拒绝，不消耗任何配额。

**记账策略与原子性（Hardening）**：

- 只有真正进入 cost-bearing workflow 的请求才计数：检查 + 自增在同一个
  `BEGIN IMMEDIATE` 事务内完成，杜绝并发 check-then-increment race；
- 超限请求不把自己计入（事务回滚），返回 `429 QUOTA_EXCEEDED` +
  `Retry-After`，并写 `quota_exceeded` 审计事件；
- `SERVICE_BUSY`、CSRF failure、authentication failure 均不消费配额；
- 已计数但生成未启动（executor 提交失败 / graceful shutdown race）时
  refund 本分钟桶（不低于 0）。
