# 当前生产架构（Phase 12 封板后）

> 当前状态：Phase 12 已于 **2026-08-28** 完成生产验收并封板。本文描述
> `https://ask.zglab.fun` 的当前生产形态；Phase 10 的历史部署基线仍可从 Git
> 历史与对应验收文档追溯。Phase 11/12 的权威设计分别见
> `docs/authentication.md`、`docs/web-research-product.md` 与
> `docs/evaluations/phase-12-production-acceptance-2026-08-28.md`。

## 1. 当前拓扑

```text
Internet
   ↓ HTTPS
ask.zglab.fun
   ↓
Nginx
   ├── Vue SPA                     /var/www/zglab-assistant
   └── FastAPI / SSE               127.0.0.1:8000
          ↓
      Security Boundary
      ├── AuthN / AuthZ / CSRF
      ├── request size / timeout
      ├── per-user quota
      ├── web_usage quota
      ├── global concurrency
      └── Web research concurrency
          ↓
      Capability Selection
      ├── PersonalKnowledgeSkill
      │      └── knowledge.db + local BGE + LLM
      └── WebResearchSkill
             └── Tavily Search
                 → SSRF/DNS validation
                 → PinnedResolutionBackend
                 → Safe Fetch / Extract
                 → ExternalEvidence
                 → shared Grounded Generation
                 → Citation Validation
```

生产公开入口以 authenticated API v2 为准：

```text
POST /api/v2/auth/login
POST /api/v2/auth/logout
GET  /api/v2/auth/me
POST /api/v2/auth/activate
POST /api/v2/auth/reset-password
POST /api/v2/auth/change-password
POST /api/v2/ask
POST /api/v2/ask/stream
```

历史匿名 `/api/v1/ask` 与 `/api/v1/ask/stream` 已在生产退役并返回
`410 API_RETIRED`，不再是消费型入口。

## 2. 服务器目录

```text
/opt/zglab-rag/
├── app/                         # 本仓库 + .venv
│   ├── config/sources.yaml
│   ├── knowledge/
│   └── deploy/
├── notes/                       # 已注册 Git 来源
├── zglab-website/               # 已注册 Git 来源
├── zglab-tools/                 # 已注册 Git 来源
├── resume-tailor-agent/         # 已注册 Git 来源
├── zglab-daily/                 # 已注册 Git 来源
├── runtime/
│   ├── knowledge.db             # Personal knowledge/index
│   ├── auth.db                  # identity/session/audit/quota，schema v3
│   ├── backups/                 # 双库原子 SQLite 快照
│   └── logs/                    # 预留；服务日志默认进入 journald
├── rollback/                    # 生产迁移/封板回滚快照
├── models/
│   └── huggingface/             # Hugging Face cache，不进 Git
└── .env                         # 仅服务器保存的生产配置与 secrets

/var/www/zglab-assistant/        # Vue 构建产物
```

`knowledge.db` 与 `auth.db` 是两个独立 lifecycle：知识索引可以重建，身份、会话、
审计与额度状态不能随索引重建而丢失。`auth.db` 当前 schema v3，包含独立
`web_usage` bucket。

## 3. API 服务与安全顺序

`zglab-rag-api.service` 以 `zglab` 用户运行 Uvicorn，仅监听
`127.0.0.1:8000`，由 Nginx 对外暴露 HTTPS。生产启动会验证核心 runtime、
`knowledge.db`、`auth.db`、Embedding 与 LLM 配置；Web Search 是可关闭的 optional
capability，Search Provider 故障不能把 Personal RAG 的 `/ready` 一并拉死。

Authenticated ask/SSE 的当前服务端顺序是：

```text
Request size / schema validation
→ Origin → Authentication → Authorization → CSRF
→ LLM kill switch
→ Question length
→ deterministic capability selection / policy
→ global concurrency
→ Web research concurrency（仅 Web）
→ personal 或 web 独立 quota
→ Skill execution
```

关键不变量：

- 匿名请求在 capability 状态暴露前就被拒绝；
- `mode=auto/personal/web` 只能选择冻结的产品能力，不能传任意 capability id；
- Personal 与 Web 使用独立额度 bucket；
- `WEB_RESEARCH_ENABLED=false` 可以单独关闭联网能力而不影响 Personal；
- Web Research 默认最多 1 个并发研究任务；
- request body 限制覆盖 ask 与全部 credential-carrying POST，包括
  `/api/v2/auth/reset-password`。

## 4. Personal Knowledge Path

```text
PersonalKnowledgeSkill
  → request-scoped read-only knowledge.db connection
  → vector retrieval（public-only）
  → Evidence Context
  → shared Grounded Generation
  → Citation Validation
  → Answer + personal sources
```

登录并不自动开放 private knowledge；生产检索继续强制公开可见边界。未来 owner-only
knowledge 属于后续 Advanced Permissions 范围。

## 5. Web Research Path

```text
WebResearchSkill
  → Tavily SearchProvider（每次 research 最多一次 search）
  → deterministic candidate selection
  → URL / DNS safety validation
  → PinnedResolutionBackend
  → bounded Safe Fetch
  → deterministic HTML/text extraction
  → ExternalEvidence（origin=web, trust=untrusted）
  → shared Grounded Generation
  → Citation Validation
  → provenance-backed web sources
```

安全边界：

- 只允许 `http` / `https`；
- loopback、RFC1918、link-local、metadata、CGNAT、unsafe IPv6 等地址拒绝；
- hostname 任一 DNS 结果不安全则整项拒绝；
- 每个 redirect hop 重新验证并重新 pin；
- 实际 TCP connection 只连接已验证的 pinned public IP；
- TLS hostname verification / SNI 与 Host header 保持原 hostname，不使用
  `verify=False`；
- response size、timeout、redirect count、content-type、extracted chars 全部 bounded；
- Web evidence 是不可信只读数据，不能成为 system/tool instruction；
- LLM 只能引用 evidence id，最终 URL 由服务端 provenance 映射，模型自造 URL 不会
  成为 source；
- ExternalEvidence 为 request-scoped，不写入长期 `knowledge.db`。

## 6. SSE

Personal：

```text
accepted → retrieving → generating → validating → completed
```

Web：

```text
accepted → researching → generating → validating → completed
```

Stage event 只携带 `request_id` 与 `stage`，不发送网页正文、URL 列表、Provider
响应、Prompt、Evidence 或内部诊断。共享 SSE helper 的 completion log 使用真实
`request.url.path`，避免把 v2 流错误标成历史 v1 path。

## 7. Nginx 与浏览器边界

`deploy/nginx/ask.zglab.fun.conf` 提供 Vue history fallback、API/SSE 反代与安全响应头。
SSE 保持 `proxy_buffering off` 与适配长请求的 timeout。生产 HTTPS 已验证 CSP、
`X-Content-Type-Options: nosniff`、`X-Frame-Options: SAMEORIGIN` 与严格 referrer policy。

Session 使用 server-side opaque token + Secure HttpOnly host-only cookie；SPA 只能持有
session-bound CSRF token，不能读取 session cookie。Web source 标题按 Vue 文本模板转义，
外链使用 `target=_blank` + `rel=noopener noreferrer`。

## 8. 配置与 secrets

生产配置位于 `/opt/zglab-rag/.env`，不进入 Git。至少包括 LLM、Auth、Web Research
开关、Search Provider 与 Search API key。Search key 只能来自服务器 secret 配置，不写入
仓库、日志、验收文档或 API response。

当前 Phase 12 生产验收后的关键策略为：

```text
WEB_RESEARCH_ENABLED=true
web quota = 3 / minute, 20 / day
web research concurrency = 1
```

示例与实际变量名以 `deploy/env/production.env.example` 和 `Settings` 为准，不在本文记录
真实 secret 值。

## 9. 备份与恢复

`zglab-rag-backup.timer` 每天执行一致性 SQLite backup，当前必须同时覆盖：

```text
knowledge.db
+ auth.db
```

备份通过 SQLite backup API 生成临时快照，fsync 后原子发布；`auth.db` 备份继承受限权限，
生产验收要求保持 `0600`。恢复前必须先停止 API，保留当前数据库副本，再恢复经过
`PRAGMA integrity_check=ok` 的目标快照并校正 `zglab:zglab` ownership。

Phase 12 最终生产验收已验证双库完整性与 auth backup 0600 权限。详细证据见
`docs/evaluations/phase-12-production-acceptance-2026-08-28.md`。

## 10. 知识同步

`zglab-rag-sync.timer` 继续负责已注册 Git knowledge sources 的 fast-forward-only 同步与
增量索引。远端不可达、checkout 不干净、解析/Embedding 失败时不得破坏上一版
`knowledge.db`。Web Research 不参与该同步，也不会把临时网页证据写回知识库。

常用命令：

```bash
cd /opt/zglab-rag/app
sudo -u zglab .venv/bin/zglab-rag sync plan
sudo -u zglab .venv/bin/zglab-rag sync status
sudo -u zglab .venv/bin/zglab-rag sync apply
```

## 11. 部署与回滚

当前生产升级继续遵循：

1. 记录部署 commit 与现有 systemd/Nginx/health 状态；
2. 对 app、frontend、配置和双数据库创建回滚快照；
3. `git pull --ff-only` + `uv sync --frozen`；
4. 执行正式 auth schema migration，不手工修改生产 SQLite；
5. 发布 Vue 静态产物；
6. `nginx -t` 后 reload，重启 API；
7. 先验证 `/health`、`/ready`、Auth 与 Personal regression；
8. 对 optional capability 做受控 smoke 后再开启；
9. 验证 SSE、quota/concurrency、kill switch 与 rollback；
10. 手动触发双库 backup 并执行 integrity check。

Web Research 的快速产品级回滚是：

```text
WEB_RESEARCH_ENABLED=false
```

它必须只关闭 Web capability，而 Personal Assistant 保持可服务。

## 12. 当前运维单元

生产应保持：

```text
zglab-rag-api.service      active
zglab-rag-backup.timer     enabled + active
zglab-rag-sync.timer       enabled + active
nginx                      active
/health                    200
/ready                     200
```

Phase 12 已封板；下一 Product Phase 为 Phase 13 — MCP Tool Runtime。MCP 第一版仍应优先
localhost/internal，不因为进入下一阶段而扩大现有公网暴露面。
