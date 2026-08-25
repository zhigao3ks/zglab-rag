# Phase 11 — Authentication & Access Control 验收

验收日期：2026-08-25。所有测试结果均为本次在本地工作区实际执行的输出，
未包含未运行的推断结果。

## 1. 范围

- 11A Identity Core：`auth.db`、用户模型、Argon2id、credential token、
  admin CLI、activation / password reset；
- 11B Session Authentication：login / logout / me、server-side session、
  Cookie 安全属性、CSRF / Origin、登录限流；
- 11C Protected API v2：认证 ask / SSE、授权、用户级限流与日配额、LLM kill
  switch、v1 retirement；
- 11D Product & Production：Public Landing / Login / Activation / Authenticated
  Assistant、审计、生产配置、auth.db 备份、迁移文档、回归。

明确未实现（非目标）：Web Research、MCP、Agent Planner/Router/Executor、
Session Memory、Owner Agent、OAuth/OIDC、CAPTCHA、Redis、邮件服务、
Web Admin Console。

## 2. 实际执行的测试

### 2.1 后端

```bash
uv run pytest -q
# 315 passed, 1 warning in 188.40s (0:03:08)
```

其中 Phase 11 新增：

```bash
uv run pytest tests/test_auth_identity.py -q   # 31 passed
uv run pytest tests/test_auth_api.py -q        # 34 passed
uv run pytest tests/test_auth_cli.py -q        # 9 passed
```

Phase 0–10 全部既有测试（241 个）继续通过，包含 public API 契约、SSE、
grounded generation、persistence、retrieval、reranking、production
operations 等回归。

### 2.2 静态检查

```bash
uv run ruff check .
# All checks passed!
```

### 2.3 前端

```bash
cd web
npm test -- --run
#  Test Files  5 passed (5)
#       Tests  66 passed (66)
npm run build
# vue-tsc --noEmit && vite build 成功，产物 dist/assets/*
```

新增 `tests/auth.test.ts`（11 个用例）：landing 展示、auth state restore、
无效登录错误、成功登录跳转、匿名路由守卫、activation 成功/失败/防重放。
既有 `app.test.ts` 改为挂载 `AssistantView`（15 个用例全部通过），
`client.test.ts` / `sse.test.ts` / `components.test.ts` 无修改通过。

## 3. Security Gates 对照

| # | Gate | 证据 |
| --- | --- | --- |
| 1 | 无 registration API | 代码库不存在 `/register` / `/signup`；账号仅 CLI 创建（`test_auth_cli.py`） |
| 2 | 未认证无法产生 LLM 消费 | `test_anonymous_v2_ask_rejected`、`test_anonymous_v2_sse_rejected_before_stream`（401 在任何生成前） |
| 3 | 管理员可 CLI 创建用户 | `test_user_create_outputs_single_use_activation_url` |
| 4 | 管理员不知道最终密码 | provisioning 只生成 token；密码由用户在 activation 页设置；CLI/DB 无密码 |
| 5 | Activation Token 单次使用 | `test_activation_token_is_single_use`、`test_activation_via_api`（replay→400） |
| 6 | Token 数据库只存 hash | `test_activation_token_hash_only_in_database`（iterdump 中无明文） |
| 7 | Argon2id | `test_activation_sets_argon2id_password_and_activates`（`$argon2id$` 前缀） |
| 8 | Session Token 只存 hash | `test_session_database_stores_hash_only` |
| 9–12 | Cookie Secure / HttpOnly / SameSite / host-only | `test_login_success_sets_secure_cookie_and_returns_csrf`（HttpOnly、SameSite=Lax、Path=/、无 Domain）、`test_login_secure_flag_in_production_mode` |
| 13 | logout 立即 revoke | `test_logout_revokes_session_immediately`（恢复旧 cookie 仍 401） |
| 14 | disable 立即 revoke | `test_disable_revokes_sessions_and_blocks_login` |
| 15 | password reset revoke | `test_password_reset_revokes_sessions`、`test_reset_password_flow_replaces_password_and_revokes_sessions` |
| 16 | anonymous ask = 401 | `test_anonymous_v2_ask_rejected` |
| 17 | anonymous SSE = 401 | `test_anonymous_v2_sse_rejected_before_stream` |
| 18 | SSE 无认证旁路 | v2 SSE 与普通 ask 共用 `_v2_ask_preflight` + `_v2_security_gate`；`test_sse_cannot_bypass_csrf`（403 JSON，未触达服务） |
| 19 | CSRF 生效 | `test_ask_rejects_wrong_csrf_token`、`test_logout_requires_csrf_for_valid_session` |
| 20 | Origin validation 生效 | `test_login_origin_rejected`、`test_ask_rejects_cross_origin`（含缺失 Origin） |
| 21 | Login 双维度限流 | `test_per_ip_login_throttling`、`test_per_username_login_throttling`（正确密码也被限流） |
| 22 | 用户级 rate limit | `test_user_minute_rate_limit`（429 QUOTA_EXCEEDED + Retry-After） |
| 23 | daily quota | `test_user_daily_quota`（含 `quota_exceeded` 审计断言） |
| 24 | concurrency guard 不退化 | Phase 9 并发测试全量通过；v2 复用同一 `ConcurrencyGuard` |
| 25 | timeout 不退化 | Phase 9 timeout 测试全量通过；v2 复用 `_collect_generation` / `_stream_events` |
| 26 | public-only retrieval 不退化 | retrieval/generation 层未改动；既有 visibility 测试全部通过 |
| 27 | LLM kill switch | `test_llm_kill_switch_blocks_ask_but_not_auth`（ask/SSE 503 SERVICE_DISABLED；login/me/logout 正常；服务计数为 0） |
| 28 | 日志/DB 无密码与 token | `test_audit_log_contains_no_secrets`、`test_audit_events_recorded_without_secrets` |
| 29 | auth.db 不进 Git | `.gitignore` 含 `*.db` / `runtime/`；默认路径在 `runtime/auth.db` |
| 30 | Landing 可匿名浏览 | landing 路由无守卫；`/health`、`/ready`、`/sources` 保持匿名 |
| 31 | 展示与链接保留 | LandingView 展示能力卡、GitHub / 个人主页入口（`tests/auth.test.ts`） |
| 32 | Phase 0–10 regression | 241 个既有测试全部通过 |

补充反枚举检查：`test_login_failure_returns_unified_error`（未知用户与错误
密码返回完全相同的 code/message）、
`test_login_rejects_pending_and_disabled_accounts_with_same_error`。

## 4. 手动检查（本地实际执行）

- `uv run python -m zglab_rag.cli auth init`、`user create/list/show/
  disable/enable/reset-password/revoke-sessions` 的行为由
  `tests/test_auth_cli.py` 经 `cli.main()` 全量驱动验证；
- `npm run build` 通过 vue-tsc 类型检查并输出 dist 产物；
- `git diff --check`：无空白错误。

生产浏览器端到端（真实 HTTPS、真实 Cookie Jar）属于部署后验收，本文档
记录迁移步骤供部署时逐项核验，不在本地伪造结果。

## 5. Production Migration Readiness（操作步骤，未实际执行）

前提：Phase 10 生产正常运行；本次迁移在服务器上进行，需 SSH 权限。

```bash
# 0. 备份现状（knowledge.db；auth.db 尚不存在）
cd /opt/zglab-rag/app
sudo -u zglab .venv/bin/zglab-rag backup

# 1. 部署新代码并安装依赖
git pull（或按既有发布流程）
sudo -u zglab uv sync --frozen

# 2. 初始化 auth.db（应用启动也会自动初始化，此步用于显式确认）
sudo -u zglab .venv/bin/zglab-rag auth init

# 3. 更新 /opt/zglab-rag/.env：加入 deploy/env/production.env.example 中
#    Phase 11 段（AUTH_*、API_V1_RETIRED=true、LLM_ENABLED=true）

# 4. Bootstrap 管理员并下发激活链接
sudo -u zglab .venv/bin/zglab-rag user create owner --role ADMIN
#    → 记录一次性 activation_url，浏览器打开并设置密码

# 5. 构建并发布前端
cd web && npm ci && npm run build
rsync -a --delete dist/ /var/www/zglab-assistant/

# 6. 安装更新的 systemd backup unit（新增 backup --auth ExecStart）
sudo cp deploy/systemd/zglab-rag-backup.service /etc/systemd/system/
sudo systemctl daemon-reload

# 7. 更新 Nginx（新增 CSP 头）并重载
sudo cp deploy/nginx/ask.zglab.fun.conf /etc/nginx/sites-available/...
sudo nginx -t && sudo systemctl reload nginx

# 8. 重启 API
sudo systemctl restart zglab-rag-api

# 9. 验证（迁移检查单）
curl -fsS https://ask.zglab.fun/health
curl -fsS https://ask.zglab.fun/ready
# 匿名访问 v1：应 410 API_RETIRED
curl -s -o /dev/null -w '%{http_code}' -X POST \
  https://ask.zglab.fun/api/v1/ask -H 'Content-Type: application/json' \
  -d '{"question":"测试"}'
# 匿名访问 v2：应 401 AUTHENTICATION_REQUIRED
curl -s -o /dev/null -w '%{http_code}' -X POST \
  https://ask.zglab.fun/api/v2/ask -H 'Content-Type: application/json' \
  -d '{"question":"测试"}'
# 浏览器：/ 可见 Landing；/login 登录；/assistant 正常问答；
# 退出登录后旧会话不可用

# 10. 为其他用户创建账号
sudo -u zglab .venv/bin/zglab-rag user create <username>
```

回滚策略：恢复旧代码 + `.env` 去除 Phase 11 段并重启即可；`auth.db` 保留
不影响旧版本。v1 退役开关可在迁移期间临时置 false 以保留匿名入口。

## 6. Security Hardening Review（2026-08-25）

Phase 11 主体交付后针对性安全强化，全部有对应服务端/前端测试：

1. **Fragment transport**：一次性 credential token 改为
   `/activate#token=...`（reset 附加 `&purpose=reset`），Vue 从
   `location.hash` 读取后立即 `history.replaceState` 清除，仅经 POST body
   提交；token 不进入服务端 URL、Nginx access log 或 Referer
   （`cli.py _activation_url`、`ActivateView.vue`、`router.ts`）；
2. **RESET_REQUIRED**：`admin_reset_password` 在签发 token 的同一事务内
   将 `users.credential_status` 置为 `RESET_REQUIRED`，登录在密码校验后
   检查该状态 → 旧密码立即失效；只有成功消费 reset token 才恢复
   `VALID`（fail-closed）；schema 升级为 version 2；
3. **Purpose-pinned endpoints**：`POST /api/v2/auth/activate` 只消费
   `ACTIVATE_ACCOUNT`，新增 `POST /api/v2/auth/reset-password` 只消费
   `RESET_PASSWORD`；跨 purpose 拒绝（cross-purpose rejection tests）；
4. **Gate precedence**：v2 ask 安全门改为 Origin → AuthN → AuthZ → CSRF →
   capability policy；匿名永远先得 401，kill switch 状态不泄露；
5. **Cookie 配置 fail-fast**：Settings 校验器拒绝 `__Host-*` +
   `Secure=false`；本地 HTTP 开发必须显式改 dev-only cookie 名；
6. **v1 退役 fail-closed**：`validate_production_security_settings` 使
   `env=production` 未置位 `api_v1_retired` 时拒绝启动；
7. **Quota 原子化**：检查 + 自增同一 `BEGIN IMMEDIATE` 事务；超限不自计；
   SERVICE_BUSY / CSRF / AuthN 失败不消费配额；提交失败 refund；
8. **备份权限**：backup 文件继承源数据库权限位（不取 umask），auth.db
   备份不会比 auth.db（0600）更宽松。

## 7. Remaining Risks（真实遗留项）

1. 登录限流为进程内状态：API 重启即清零（单实例场景可接受，已在
   `docs/authentication.md` Known Limitations 记录）；
2. 生产浏览器端到端验证（真实 HTTPS cookie、CSRF 头往返）待部署时执行；
3. 本地纯 HTTP 开发需要临时 cookie 配置（Secure/__Host- 限制），已在文档
   明确，且配置层 fail-fast 拒绝 `__Host-*` + `Secure=false` 弱组合；
4. `usage` 表暂无定时清理（数据量极小，接口已预留 `prune_old_usage`）；
5. fragment 传输依赖用户浏览器完整执行 JS；若激活页 JS 未运行，token
   仍会停留在浏览器历史记录中的 `/activate` 条目（不含 token 本身）与
   用户自己剪贴的链接中——服务端无泄露面。

不存在其他已知的认证旁路或数据泄露项；未发现 private knowledge 边界变化。
