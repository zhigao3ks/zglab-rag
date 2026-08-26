# Phase 11 生产部署实录与复用手册（2026-08-26）

> 本文记录 ZGLab RAG Phase 11（Authentication & Access Control）在 `ask.zglab.fun` 的一次真实生产部署过程。
>
> 目的不是重复 `docs/production-architecture.md` 的目标架构，而是沉淀实际生产环境、部署位置、软件版本、发布方式、踩坑、回滚点、验收步骤和下次部署推荐做法。
>
> 本次上线代码基线：`7d8f5a0ef6f440a3fc88acbb2922c196aacf8f73`（`feat: complete phase 11 authentication and access control`）。

## 1. 最终结论

2026-08-26，Phase 11 已完成生产上线和真实 HTTPS 浏览器验收。

最终验证通过：

- `/health`：200；
- `/ready`：200；
- `/api/v1/ask`：410 `API_RETIRED`；
- `/api/v2/ask` 匿名：401 `AUTHENTICATION_REQUIRED`；
- `/api/v2/auth/me` 匿名：401；
- ADMIN 一次性激活成功，URL fragment 会立即从浏览器地址栏清除；
- 密码落库为 Argon2id hash；
- 登录 Cookie：`__Host-zglab_session`、Secure、HttpOnly、SameSite=Lax、Path=/、host-only；
- `document.cookie` 无法读取 session；
- `/api/v2/auth/me` 登录态恢复成功；
- 缺失 CSRF 的 Ask 请求返回 403 `CSRF_REJECTED`；
- 正常 Ask 和 SSE 均成功；
- logout 后 Cookie 消失，旧 session 立即失效，`/auth/me` 返回 401；
- `knowledge.db` 与 `auth.db` 双库备份均成功且 SQLite `integrity_check=ok`；
- `auth.db` 及其备份保持 0600 权限；
- backup timer、sync timer、API、Nginx 最终均为 active。

因此 Phase 11 可以视为生产验收完成。

---

## 2. 生产服务器信息

### 2.1 主机与资源

本次生产服务器实际环境：

| 项目 | 值 |
| --- | --- |
| OS | Ubuntu 24.04.4 LTS |
| CPU | 2 vCPU |
| RAM | 2 GiB |
| Swap | 2 GiB |
| 系统盘 | 40 GiB |
| 公网入口 | `https://ask.zglab.fun` |
| API 监听 | `127.0.0.1:8000` |
| SSH 运维用户 | `ubuntu` |
| 应用运行用户 | `zglab` |

不建议在 Git 文档中固化公网 IP；服务器寻址以 DNS `ask.zglab.fun` 和云平台资产记录为准，避免实例迁移后文档失效。

### 2.2 本次实际确认的软件版本

| 软件 | 版本 / 状态 | 说明 |
| --- | --- | --- |
| Python | 3.12.3 | 生产 `.venv/bin/python` 最终链接系统 Python |
| uv | 0.12.4 | `/usr/local/bin/uv` |
| Server Node.js | 18.19.1 | 不能构建当前 Vite 7 前端 |
| Server npm | 9.2.0 | 与 Node 18 配套 |
| Local/WSL Node.js | 24.18.0 | 本次前端实际构建环境 |
| Local/WSL npm | 11.16.0 | 本次前端实际构建环境 |
| Vite | 7.3.6 | 要求 Node `^20.19.0 || >=22.12.0` |
| argon2-cffi | 25.1.0 | Phase 11 新增认证依赖 |
| argon2-cffi-bindings | 26.1.0 | Argon2 底层绑定 |
| Nginx | 已部署并正常运行 | 本次部署日志未单独记录 `nginx -v` 输出；下次部署建议纳入 preflight |

下次上线前建议统一采集：

```bash
lsb_release -a
python3 --version
uv --version
node --version
npm --version
nginx -v
```

---

## 3. 生产目录与服务位置

### 3.1 应用目录

生产根目录：

```text
/opt/zglab-rag/
├── app/                         # 部署后的应用代码；不是 Git checkout
│   ├── .venv/                   # 生产 Python 虚拟环境
│   ├── src/
│   ├── config/
│   ├── deploy/
│   └── web/
├── runtime/
│   ├── knowledge.db             # RAG 知识索引
│   ├── auth.db                  # Phase 11 身份 / session / token / audit
│   └── backups/
├── staging/                     # 发布前 staging
├── rollback/                    # 手工生产回滚快照
├── notes/                       # knowledge source checkout
├── zglab-website/
├── zglab-tools/
├── resume-tailor-agent/
├── zglab-daily/
└── .env                         # 生产私有配置，不入 Git
```

前端静态资源：

```text
/var/www/zglab-assistant/
```

### 3.2 systemd

主要 unit：

```text
/etc/systemd/system/zglab-rag-api.service
/etc/systemd/system/zglab-rag-backup.service
/etc/systemd/system/zglab-rag-backup.timer
/etc/systemd/system/zglab-rag-sync.service
/etc/systemd/system/zglab-rag-sync.timer
```

API unit 关键约束：

```text
User=zglab
Group=zglab
WorkingDirectory=/opt/zglab-rag/app
EnvironmentFile=/opt/zglab-rag/.env
PYTHONPATH=/opt/zglab-rag/app/src
```

API 通过以下方式启动，而不是依赖 console entry point：

```text
/opt/zglab-rag/app/.venv/bin/python -m uvicorn ...
```

### 3.3 Nginx

本机实际生产配置位置：

```text
/etc/nginx/conf.d/ask.zglab.fun.conf
```

仓库模板：

```text
deploy/nginx/ask.zglab.fun.conf
```

证书目录：

```text
/etc/letsencrypt/live/ask.zglab.fun/
```

### 3.4 备份

生产备份目录：

```text
/opt/zglab-rag/runtime/backups/
```

Phase 11 后每日 backup service 连续执行：

```text
knowledge.db -> knowledge-<UTC timestamp>.db
auth.db      -> auth-<UTC timestamp>.db
```

`zglab-rag-backup.service` 是 `Type=oneshot`，执行完成后显示 `inactive (dead)` 是正常状态；应检查最近一次执行是否 `status=0/SUCCESS`。

---

## 4. 本次发布制品

生产 `/opt/zglab-rag/app` **不是 Git 仓库**，因此本次没有在服务器执行 `git pull`。

后端从本地 WSL 的已确认 commit 创建 Git archive：

```bash
git archive \
  --format=tar.gz \
  --output=/tmp/zglab-rag-phase11-7d8f5a0.tar.gz \
  7d8f5a0
```

SHA-256：

```text
8c5f5fe382721ed23fe787ecbb04d1ab0e0de8321be77f3b9a32b97f43cc4cde
```

前端在本地 WSL Node 24 构建后单独打包：

```text
/tmp/zglab-rag-phase11-web-7d8f5a0.tar.gz
```

SHA-256：

```text
385a508790729fd11aa330db5e4be546e4f161e3401eb0aa565b7f8a7673a137
```

生产前应同时验证：

```bash
sha256sum /tmp/zglab-rag-phase11-7d8f5a0.tar.gz
sha256sum /tmp/zglab-rag-phase11-web-7d8f5a0.tar.gz
```

推荐继续保持这种“**精确 commit -> immutable archive -> checksum -> staging -> production**”方式，而不是把服务器临时变成开发 Git 工作区。

---

## 5. 回滚基线

本次上线前创建了完整回滚目录：

```text
/opt/zglab-rag/rollback/pre-phase11-20260826T011754Z/
```

包含：

```text
app.tar.gz           # Phase 10 app，排除 .venv
venv.tar.gz          # 原生产 venv
frontend.tar.gz      # 原生产前端
production.env       # 原生产 .env，0600
systemd unit files
ask.zglab.fun.conf   # 原 Nginx 配置
```

同时在上线前单独执行 SQLite backup，得到：

```text
/opt/zglab-rag/runtime/backups/knowledge-20260826T011701Z.db
```

推荐原则：

1. 代码、虚拟环境、前端、`.env`、Nginx/systemd 配置都应有 rollback snapshot；
2. 数据库另外使用 SQLite backup API 做一致性快照；
3. rollback 目录权限至少 0700；
4. 不要把秘密、token、`.env` 内容提交到 Git。

---

## 6. Phase 11 配置要点

生产 `.env` 在原 Phase 10 配置基础上新增：

```text
ZGLAB_RAG_ENV=production
ZGLAB_RAG_API_V1_RETIRED=true
ZGLAB_RAG_AUTH_DATABASE_PATH=/opt/zglab-rag/runtime/auth.db
ZGLAB_RAG_AUTH_PUBLIC_BASE_URL=https://ask.zglab.fun
ZGLAB_RAG_AUTH_ALLOWED_ORIGINS=["https://ask.zglab.fun"]
ZGLAB_RAG_AUTH_COOKIE_NAME=__Host-zglab_session
ZGLAB_RAG_AUTH_COOKIE_SECURE=true
ZGLAB_RAG_LLM_ENABLED=true
```

以及 session/token/password/login-throttle/quota 等 Phase 11 参数。

本次没有直接覆盖 `.env`，而是先创建：

```text
/opt/zglab-rag/.env.phase11.candidate
```

要求：

```text
owner: zglab:zglab
mode: 0600
```

并使用 Phase 11 `config.py` 实际解析 candidate，确认：

```text
env = production
api_v1_retired = true
auth_database_path = /opt/zglab-rag/runtime/auth.db
auth_cookie_name = __Host-zglab_session
auth_cookie_secure = true
llm_enabled = true
llm_provider_configured = true
```

只有 candidate 验证通过，才在维护窗口中原子安装为正式 `/opt/zglab-rag/.env`。

---

## 7. 真实踩坑与推荐解法

这是本文最重要的部分。

### 7.1 生产 `app/` 不是 Git checkout

#### 现象

`/opt/zglab-rag/app` 没有 `.git`，无法按普通服务器仓库流程 `git pull`。

#### 原因

Phase 10 生产环境实际采用“发布副本”而不是长期 Git working tree。

#### 推荐

继续使用：

```text
local exact commit
-> git archive
-> sha256
-> scp/upload
-> staging extract
-> rsync production
```

优点：

- 发布内容可追溯到精确 SHA；
- 不把生产机变成开发工作区；
- 不会因为本地 uncommitted changes 影响部署；
- rollback 更直接。

---

### 7.2 不要再次复制本地 WSL `.venv` 到服务器

#### 现象

旧生产 `.venv/bin/zglab-rag` shebang 指向：

```text
/home/zhigao/projects/zglab-rag/.venv/bin/python3
```

这是本地 WSL 路径，在服务器不存在。

但 `.venv/bin/python` 最终使用服务器 `/usr/bin/python3`，所以 systemd 通过：

```text
.venv/bin/python -m ...
```

仍可正常工作。

#### 原因

Python console script shebang 在创建虚拟环境时写死了解释器路径；跨机器复制 venv 不可靠。

#### 推荐

以后部署：

- 永远 `--exclude='.venv/'`；
- 生产 venv 在服务器原生创建 / 维护；
- systemd 优先使用 `.venv/bin/python -m module`；
- 不依赖跨机器复制后的 console entry point；
- 长期应安排一次“服务器原生重建 `.venv`”维护，而不是继续继承历史复制产物。

---

### 7.3 Phase 11 只新增 Argon2 时，不要无必要全量重建 1.2 GiB venv

#### 现象

生产 venv 约 1.2 GiB；Phase 11 Python 依赖差异仅新增 Argon2 相关依赖。

#### 本次做法

先 `uv pip check` 确认原环境一致，再最小安装：

```text
argon2-cffi==25.1.0
argon2-cffi-bindings==26.1.0
cffi==2.1.1
pycparser==3.0
```

安装后再次：

```bash
uv pip check
```

并执行 Argon2id hash/verify smoke test。

#### 推荐

对于小版本上线：

- 先比较 lockfile / dependency diff；
- 若只有少量新增依赖且生产 venv 很大，可做最小增量安装；
- 但长期仍应将“服务器原生、`uv sync --frozen` 可重建”作为最终目标。

---

### 7.4 `sudo -u zglab uv ...` 必须注意当前工作目录

#### 现象

从 `/home/ubuntu` 直接执行：

```bash
sudo -u zglab uv ...
```

时，uv 尝试访问 `/home/ubuntu/uv.toml`，出现 permission denied。

#### 推荐

始终先进入应用用户可访问目录：

```bash
sudo -u zglab bash -c '
  cd /opt/zglab-rag/app &&
  uv ...
'
```

同类规则也适用于任何会自动查找当前目录配置文件的 CLI。

---

### 7.5 Phase 11 CLI 从 `/home/ubuntu` 运行会触发 `.env` PermissionError

#### 现象

第一次 auth smoke test：

```text
PermissionError: [Errno 13] Permission denied: '.env'
```

`auth.db` 没有成功初始化。

#### 原因

Settings 使用：

```text
env_file=.env
```

`sudo -u zglab` 仍保留 `/home/ubuntu` 作为 cwd，Pydantic 尝试读取当前目录 `.env`，而 `zglab` 无权限访问 `/home/ubuntu`。

#### 推荐

CLI 应明确切换到应用或 staging 目录：

```bash
sudo -u zglab bash -c '
  cd /opt/zglab-rag/staging/phase11-<sha> &&
  PYTHONPATH=... python -m zglab_rag.cli auth init
'
```

并通过 `env KEY=value` 显式注入 smoke test 配置。

**不要简单 `source /opt/zglab-rag/.env`** 来解决，因为类似：

```text
ZGLAB_RAG_AUTH_ALLOWED_ORIGINS=["https://ask.zglab.fun"]
```

的 JSON 字符串在 shell source 后可能丢失需要的引号语义。生产服务仍应由 systemd `EnvironmentFile=` 加载。

---

### 7.6 Server Node 18 无法构建当前 Vite 7 前端

#### 现象

服务器：

```text
Node 18.19.1
npm 9.2.0
```

`npm ci` 可以完成，但：

```text
Vite 7.3.6 requires Node ^20.19.0 || >=22.12.0
crypto.hash is not a function
```

导致 `npm run build` 失败。

#### 本次解决

没有为了单个项目去修改服务器全局 Node。

改为在本地 WSL：

```text
Node 24.18.0
npm 11.16.0
```

执行：

```bash
cd ~/projects/zglab-rag/web
npm ci
npm run build
```

成功后把 `dist/` 独立打包、计算 SHA256、上传服务器。

#### 推荐

生产发布优先级：

1. CI / build host 构建前端 artifact；
2. 或本地受控 Node 版本构建；
3. 服务器只负责发布静态 artifact；
4. 不要为了临时构建随意升级生产机全局 Node。

如果未来希望服务器自建，可单独安装项目级 Node 22 LTS，而不是替换系统 Node。

---

### 7.7 Nginx `add_header` 继承是本次最隐蔽的线上问题

#### 现象

Phase 11 Nginx 模板在 `server {}` 级新增：

```text
X-Content-Type-Options
X-Frame-Options
Referrer-Policy
Content-Security-Policy
```

`nginx -t` 完全成功，但真实请求：

```bash
curl -D - https://ask.zglab.fun/
```

看不到任何这些安全头。

#### 原因

Nginx `add_header` 的继承规则：

如果某个 `location` 自己定义了任何 `add_header`，它不会继续继承上层 `server` 中的 `add_header`。

本配置中：

```text
location /          -> add_header Cache-Control ...
static asset loc    -> add_header Cache-Control ...
location /api/      -> add_header X-Accel-Buffering ...
```

因此 server-level 安全 Header 实际被覆盖。

#### 生产 hotfix

创建：

```text
/etc/nginx/snippets/zglab-security-headers.conf
```

内容：

```nginx
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'" always;
```

并在所有自身拥有 `add_header` 的 location 内显式 include：

```nginx
include /etc/nginx/snippets/zglab-security-headers.conf;
```

然后：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

真实公网验证后 Header 已出现。

#### 推荐

以后不能把：

```text
nginx -t 成功
```

等价为：

```text
浏览器真的收到了安全 Header
```

必须增加：

```bash
curl -sS -D - -o /dev/null https://ask.zglab.fun/ \
  | grep -iE 'content-security-policy|x-content-type-options|x-frame-options|referrer-policy'
```

**注意：当前部署后生产机已经 hotfix；后续部署模板也必须保持同样的继承处理，否则重新发布 Nginx 配置会覆盖生产修复。**

---

### 7.8 静态 JS 出现两条 Cache-Control

真实验证时 JS 返回：

```text
cache-control: max-age=604800
cache-control: public, max-age=604800, immutable
```

原因是：

- `expires 7d` 自己会生成缓存相关 Header；
- 配置中又显式 `add_header Cache-Control ...`。

这不影响本次认证安全验收，但建议后续整理为单一明确缓存策略，减少歧义。

---

## 8. auth.db 初始化与管理员 Bootstrap

### 8.1 先 smoke test，再创建正式库

本次在生产切换前先用 staging source 创建临时：

```text
/opt/zglab-rag/staging/auth-smoke/auth-smoke.db
```

验证：

```text
schema_version = 2
mode = 0600
ADMIN status = PENDING
password_hash = NULL
credential_tokens = 1
audit_events >= 1
```

通过后删除临时库。

这种做法可以提前发现：

- SQLite schema 问题；
- 权限问题；
- CLI 导入问题；
- Argon2 / identity dependency 问题；
- production settings 解析问题。

### 8.2 正式 auth.db

正式路径：

```text
/opt/zglab-rag/runtime/auth.db
```

首次创建后必须：

```text
owner = zglab:zglab
mode = 0600
schema_version = 2
```

### 8.3 ADMIN

正式 ADMIN 通过 CLI 预创建，初始：

```text
role = ADMIN
status = PENDING
password_hash = NULL
```

CLI 只打印一次 activation URL：

```text
https://ask.zglab.fun/activate#token=<one-time-token>
```

实际 token 不应粘贴到聊天、工单、日志或 Git。

本次先临时保存到 0600 文件，激活成功后立即删除。

浏览器激活验收：

- `#token=...` 很快被 `history.replaceState` 清除；
- 用户在浏览器设置最终密码；
- DB 中状态变 `ACTIVE`；
- password hash 前缀为 `$argon2id$`；
- activation token `consumed_at` 非空。

---

## 9. 推荐的正式切换顺序

对于同类“认证/安全边界”升级，推荐分成“在线准备”和“短维护窗口”两部分。

### 9.1 在线准备阶段（不中断 Phase 10）

依次完成：

1. 记录当前服务、磁盘、版本；
2. SQLite knowledge backup；
3. 完整 rollback snapshot；
4. 构建 exact commit release archive；
5. 上传并校验 SHA256；
6. 解压到 staging；
7. dependency diff；
8. 安装新增依赖并 `uv pip check`；
9. `rsync --dry-run --delete` 审查删除项；
10. 本地/CI 构建前端，上传 artifact；
11. `.env.phase11.candidate` 解析验证；
12. Nginx candidate 独立语法测试；
13. systemd unit verify；
14. 临时 auth.db smoke test；
15. 正式 auth.db 初始化和 ADMIN provisioning。

尽可能把可能失败的步骤移出维护窗口。

### 9.2 短维护窗口

推荐顺序：

```text
stop API
-> rsync backend（保留 .venv）
-> clean old pycache
-> install candidate .env
-> publish frontend artifact
-> install backup unit
-> install nginx config
-> systemctl daemon-reload
-> nginx -t
-> Phase 11 import/config preflight
-> start API
-> /health
-> /ready
-> reload Nginx
```

本次实际停机窗口约为分钟级，主要风险均已在 staging 阶段消除。

---

## 10. rsync 推荐规则

后端实际发布时必须保护生产 venv：

```bash
sudo rsync -a --delete \
  --exclude='.venv/' \
  --exclude='web/node_modules/' \
  "$STAGE/" \
  /opt/zglab-rag/app/
```

上线前 dry-run 应再额外检查：

```bash
--dry-run --itemize-changes
```

本次 dry-run 中所有 deletion 都是：

```text
__pycache__/
*.pyc
```

没有业务源文件被误删。

推荐以后 staging 验证时不要污染 staging 生成 pycache，或者正式 rsync 时直接排除：

```text
--exclude='__pycache__/'
--exclude='*.pyc'
```

---

## 11. Nginx 预检方法

Phase 11 site 配置依赖证书路径，不能简单把第二份 `.conf` 丢进 `/etc/nginx/conf.d/` 测试，否则会出现重复 server。

本次使用临时完整 Nginx config：

```nginx
worker_processes 1;
pid /tmp/zglab-phase11-nginx-test.pid;

events {
    worker_connections 64;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    access_log off;
    error_log /tmp/zglab-phase11-nginx-test-error.log;

    include /opt/zglab-rag/staging/phase11-<sha>/deploy/nginx/ask.zglab.fun.conf;
}
```

然后：

```bash
sudo nginx -t -c /tmp/zglab-phase11-nginx-test.conf -p /
```

这样可以在不替换线上 Nginx 配置的情况下验证候选文件语法。

注意：语法测试无法发现 `add_header` 继承造成的“Header 实际缺失”，因此上线后仍必须 HTTP 层验证。

---

## 12. 生产验收清单

### 12.1 匿名态

```text
GET  /health             -> 200
GET  /ready              -> 200
POST /api/v1/ask         -> 410 API_RETIRED
POST /api/v2/ask         -> 401 AUTHENTICATION_REQUIRED
GET  /api/v2/auth/me     -> 401 AUTHENTICATION_REQUIRED
GET  /                   -> 200
```

同时确认 HTML 引用的是新前端 hash asset。

### 12.2 Security headers

公网响应必须实际包含：

```text
Content-Security-Policy
X-Content-Type-Options: nosniff
X-Frame-Options: SAMEORIGIN
Referrer-Policy: strict-origin-when-cross-origin
```

不要只检查 Nginx 配置文件。

### 12.3 激活与 Cookie

激活：

```text
PENDING -> ACTIVE
password_hash -> Argon2id
activation token -> consumed
```

登录 Cookie：

```text
Name: __Host-zglab_session
Secure: true
HttpOnly: true
SameSite: Lax
Path: /
Domain: host-only ask.zglab.fun
```

浏览器：

```javascript
document.cookie
```

不应看到 session。

### 12.4 CSRF

登录态下缺失 `X-CSRF-Token`：

```text
POST /api/v2/ask -> 403 CSRF_REJECTED
```

正常前端请求必须自动携带 CSRF token。

### 12.5 Ask / SSE / Logout

验证：

- 普通 Ask 200；
- SSE 200，阶段事件正常完成；
- logout 后 Cookie 消失；
- logout 后 `/auth/me` 401；
- 已退出的旧 session 不可恢复。

---

## 13. 双数据库备份验收

手动执行：

```bash
sudo systemctl start zglab-rag-backup.service
```

本次实际结果：

```text
knowledge-20260826T024817Z.db
  mode: 0644
  size: 9969664 bytes

auth-20260826T024817Z.db
  mode: 0600
  size: 61440 bytes
```

两份备份：

```text
PRAGMA integrity_check -> ok
```

`auth` 备份内容确认：

```text
schema_version = 2
ADMIN = ACTIVE
```

权限差异是设计结果：backup 会继承源数据库权限，因此敏感的 `auth.db` backup 不会比源文件更宽松。

Timer 最终状态：

```text
zglab-rag-backup.timer: enabled + active
zglab-rag-sync.timer:   enabled + active
```

---

## 14. 本次上线后的已知非阻塞项

### 14.1 Nginx 静态资源 Cache-Control 重复

如前所述，静态文件响应当前可能出现两条缓存 Header。建议后续只保留一种明确策略。

### 14.2 Nginx 历史 warning

`nginx -t` 会看到类似：

```text
protocol options redefined for [::]:443 in /etc/nginx/sites-enabled/zglab.fun:36
```

这是服务器其他站点的历史配置 warning，本次 Phase 11 并未引入，且 Nginx test successful。

建议未来统一整理各站点 `listen 443 ssl http2` / IPv6 options，消除噪声。

### 14.3 `tat_agent.service` 历史 warning

`systemd-analyze verify` 会看到：

```text
PIDFile= references a path below legacy directory /var/run/
```

来源是服务器其他服务 `tat_agent.service`，与 zglab-rag 无关。

不要因为全局 verify 输出中的其他 unit warning 阻塞本项目部署，但应该在服务器维护清单中单独治理。

### 14.4 login throttle 为单进程内存状态

API 重启会清零登录限流状态。当前单实例、低流量场景可接受；未来多实例或更强安全需求应迁移到 Redis / shared store。

### 14.5 usage 表暂未定时清理

当前量级很小，不影响 Phase 11；长期可以增加 maintenance/prune。

---

## 15. 下次部署推荐 checklist

上线前：

```text
[ ] 精确记录 commit SHA
[ ] 服务器版本与容量 preflight
[ ] /health /ready 当前正常
[ ] knowledge.db 一致性备份
[ ] 代码 / venv / frontend / env / systemd / nginx rollback snapshot
[ ] release archive SHA256
[ ] staging 解压及关键文件检查
[ ] dependency diff + uv pip check
[ ] rsync --dry-run --delete
[ ] 前端在兼容 Node 版本构建
[ ] frontend artifact SHA256
[ ] candidate .env 真实 Settings parse
[ ] systemd-analyze verify
[ ] standalone nginx candidate test
[ ] auth smoke（涉及认证升级时）
```

切换时：

```text
[ ] stop API
[ ] 发布 backend，绝不覆盖 .venv
[ ] install .env candidate
[ ] 发布 frontend artifact
[ ] 更新 systemd unit
[ ] 更新 Nginx
[ ] nginx -t
[ ] Phase 11 import/config preflight
[ ] start API
[ ] /health
[ ] /ready
[ ] reload Nginx
```

切换后：

```text
[ ] v1 retirement = 410
[ ] v2 anonymous = 401
[ ] /auth/me anonymous = 401
[ ] security headers 真实公网存在
[ ] activation fragment 清除
[ ] Argon2id
[ ] Secure/HttpOnly/SameSite/host-only Cookie
[ ] CSRF 403 negative test
[ ] Ask 200
[ ] SSE completed
[ ] logout 后旧 session 401
[ ] knowledge/auth 双备份
[ ] backup integrity_check
[ ] auth backup 0600
[ ] backup/sync timers active
[ ] API/Nginx active
```

---

## 16. 最重要的复用经验

这次部署最大的经验不是某一条命令，而是把生产上线拆成四个层次：

```text
1. Immutable release
   exact commit + artifact + checksum

2. Preflight outside downtime
   staging + config parse + dependency + smoke + dry-run

3. Minimal maintenance window
   只做已经验证过的文件切换和 restart

4. Real behavior acceptance
   不只看进程和语法，要看真实 HTTPS / Cookie / CSRF / Header / backup
```

特别是安全功能上线，以下两类“看起来成功”不能当成真实验收：

```text
nginx -t 成功 != 安全 Header 已下发
systemctl active != 认证链路已经安全闭环
```

最终必须验证浏览器和 HTTP 层的真实行为。

---

## 17. 相关文档

- `docs/production-architecture.md`：生产架构与目录约定；
- `docs/authentication.md`：Phase 11 身份、session、Cookie、CSRF、安全设计；
- `docs/api-v2.md`：认证 API v2 契约；
- `docs/evaluations/phase-11-authentication-acceptance.md`：本地功能与安全验收；
- `deploy/env/production.env.example`：生产配置模板；
- `deploy/systemd/`：生产 systemd 资产；
- `deploy/nginx/`：生产 Nginx 资产。

本文是对上述设计文档的“真实生产运行补充”，后续 Phase 12+ 部署可以继续沿用同一发布与验收框架。
