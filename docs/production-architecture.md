# Phase 10 生产架构

## 1. 目标与边界

生产环境以最少组件长期运行公开的 ZGLab Personal Knowledge Assistant：Nginx 提供 HTTPS 和
Vue SPA，FastAPI 提供既有 Public API v1 与 SSE，SQLite 保存权威知识索引。此阶段不改变
ingestion、chunking、Embedding、Retrieval、Generation 或 Citation 的既有行为与契约。

```text
Internet
   ↓ HTTPS
ask.zglab.fun
   ↓
Nginx ── 静态文件 ── /var/www/zglab-assistant
   ↓ /api/*、/health、/ready
FastAPI / Uvicorn（127.0.0.1:8000）
   ↓
ZGLab RAG Runtime
   ├── runtime/knowledge.db
   ├── models/huggingface/
   └── OpenAI-compatible LLM Provider
```

主站 `zglab.fun` 与工具站 `tools.zglab.fun` 保持现有用途；助手只使用固定域名
`ask.zglab.fun`。

## 2. 服务器目录

```text
/opt/zglab-rag/
├── app/                         # 本仓库与 .venv，只读部署代码
│   ├── config/sources.yaml
│   ├── knowledge/identity/
│   └── deploy/
├── notes/                       # 已注册 Git 来源，对应 ../notes
├── zglab-website/               # 已注册 Git 来源
├── zglab-tools/                 # 已注册 Git 来源
├── resume-tailor-agent/         # 已注册 Git 来源
├── zglab-daily/                 # 已注册 Git 来源
├── runtime/
│   ├── knowledge.db             # SQLite 权威索引
│   ├── backups/                 # 原子 SQLite 快照，保留最近 7 份
│   └── logs/                    # 预留给运行日志；服务日志默认进入 journald
├── models/
│   └── huggingface/             # Hugging Face cache，不进入 Git
└── .env                         # 仅服务器保存的 LLM 与生产配置

/var/www/zglab-assistant/        # Vue 构建产物
```

`config/sources.yaml` 的 Git `local_path` 相对于 `/opt/zglab-rag/app` 解析，因此将来源
checkout 放在 `/opt/zglab-rag/` 下。只同步已注册的来源；不扫描服务器上的其他仓库。

## 3. 服务与启动

`zglab-rag-api.service` 以用户 `zglab` 运行 Uvicorn，仅监听 `127.0.0.1:8000`。它加载
`/opt/zglab-rag/.env`，预先加载 Embedding 模型、检查 SQLite 索引和 LLM 配置；在完成前
不会将 `/ready` 标记为可用。服务异常退出自动重启，使用 `SIGINT` 与 120 秒停止期限处理
优雅关闭。

`/health` 只表示进程存活；`/ready` 表示 runtime 初始化、数据库访问、Embedding Provider
初始化和 LLM 配置检查均已成功。Nginx 只向公网提供静态站点、`/api/`、`/health` 和 `/ready`。

应用访问日志以 JSON 写入 journald，字段为 `request_id`、`path`、`latency_ms`、`status` 和
`error_code`。日志不记录 API Key、Prompt、Evidence、完整用户问题或内部绝对路径。

## 4. Nginx 与 SSE

`deploy/nginx/ask.zglab.fun.conf` 是 HTTPS 最终配置。它使用 Vue 的 `try_files` history
fallback、gzip 和静态资源缓存。`/api/` 反代到本机 Uvicorn，并显式设置：

- `proxy_buffering off`；
- `proxy_http_version 1.1`；
- 120 秒 `proxy_read_timeout` 与 `proxy_send_timeout`；
- `X-Accel-Buffering: no`。

因此 Phase 9B 的 SSE status event 不会被 Nginx 缓冲。证书首次签发前使用
`ask.zglab.fun.bootstrap.conf` 只开放 ACME challenge；签发后替换为 HTTPS 配置。

## 5. 备份与恢复

`zglab-rag backup` 使用 SQLite backup API 生成一致性快照。先写入 `runtime/backups/` 中的
临时文件，完成并 fsync 后通过原子 rename 发布，保留最近 7 份。systemd 的
`zglab-rag-backup.timer` 每天 02:45 运行。

恢复步骤：停止 API 服务，将经过核验的备份复制为 `runtime/knowledge.db`，确保属主为
`zglab:zglab` 后启动 API。恢复前不要删除原数据库；先移动为带时间戳的保留文件。

## 6. 知识同步

运维命令：

```bash
cd /opt/zglab-rag/app
sudo -u zglab .venv/bin/zglab-rag sync plan
sudo -u zglab .venv/bin/zglab-rag sync status
sudo -u zglab .venv/bin/zglab-rag sync apply
```

`plan` 只读取注册来源和现有索引，输出每个来源的 revision、new、changed、removed 和
unchanged Chunk。`apply` 在更新前创建备份，再复用既有的增量 indexing 事务；获取、解析或
Embedding 失败时，旧索引保持可用且失败运行会被审计。成功后由
`zglab-rag-sync.service` 重启 API，从而加载新索引。`zglab-rag-sync.timer` 每天 03:15 运行。

`apply` 只对已注册 Git checkout 执行有 60 秒上限的 `git fetch --prune` 与
fast-forward-only 更新，绝不 clone 或扫描其他仓库。远端不可达、工作区不干净或更新失败时，
ingestion 不会开始，旧 `knowledge.db` 继续对外服务。首次已预置 checkout 的建库可显式使用
`sync apply --skip-git-fetch`；它只索引当前 checkout，不应替代常规定时同步。

## 7. 部署顺序

1. 创建 `zglab` 系统用户和 `/opt/zglab-rag` 目录；
2. 部署 app、来源 checkout 与私有 `.env`；
3. 执行 `uv sync --frozen`，预下载生产模型并首次建立索引；
4. 构建 Vue 并发布到 `/var/www/zglab-assistant`；
5. 安装 systemd unit/timer 与 Nginx bootstrap 配置；
6. 通过 ACME 为 `ask.zglab.fun` 获取证书，替换为 HTTPS Nginx 配置；
7. 启动 API、timer 和 Nginx，验证 `/health`、`/ready`、普通 API 与 SSE；
8. 记录启动时间、RSS、请求延迟和故障恢复结果。

部署资产位于 `deploy/`，敏感 `.env`、数据库、模型、备份、日志和前端构建产物均不进入 Git。
