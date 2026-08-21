# Phase 10 生产验收

本文件记录生产化部署的验收矩阵。所有实际时间、状态和延迟应在部署后填入；不得用本地模拟结果
替代公网验证结果。

## 验收矩阵

| 范围 | 验收项 | 验证命令或地址 | 结果 |
|---|---|---|---|
| systemd | API 服务为 active | `systemctl status zglab-rag-api` | 通过：enabled、active (running) |
| systemd | 进程重启后恢复 ready | `systemctl restart zglab-rag-api` | 通过：8.215 秒恢复 `/ready` |
| Nginx | 配置语法正确且服务 active | `nginx -t`、`systemctl status nginx` | 通过 |
| HTTPS | 证书和域名可访问 | `https://ask.zglab.fun` | 通过：HTTP/2 200，证书至 2026-11-19 |
| API | liveness | `GET /health` | 通过：200 |
| API | readiness | `GET /ready` | 通过：200，`ready` |
| API | Public API v1 | `POST /api/v1/ask` | 通过：200 `answered`，含公开 Sources |
| SSE | 事件顺序与完成事件 | `POST /api/v1/ask/stream` | 通过：accepted → retrieving → generating → validating → completed |
| Web | Vue 页面与 history fallback | `https://ask.zglab.fun` | 通过：根路径返回 Vue HTML；构建产物已发布 |
| Web | Sources 与 insufficient 状态 | API 与浏览器手工复核 | 通过：正常回答含 Sources；虚构问题返回 `insufficient_evidence` 与空 Sources |
| Backup | 原子备份与保留策略 | `zglab-rag backup` | 通过：生成 SQLite 快照；每日 timer 已启用 |
| Sync | plan/status/apply 与失败安全 | `zglab-rag sync ...` | 通过：首次 1,058 Chunk 建库；Git 超时 60 秒后退出，索引未打开且数据库保持不变 |
| LLM 故障 | 对外返回受控错误 | 临时断开 Provider 后调用 API | 未在生产密钥上执行破坏性验证；已有 API 单测覆盖 |

## 记录格式

```text
部署日期：2026-08-21
部署 revision：e38a801 加本次未提交的 Phase 10 工作树
系统启动耗时：约 5.6～8.2 秒（模型缓存已预置）
稳定 RSS：约 439 MiB
健康检查延迟：公网域名测试 0.28 秒（一次测量）
普通回答延迟：28.094 秒（一次带 LLM 的端到端测量）
SSE 首个事件延迟：连接后立即收到 accepted（未单独计时）
SSE 完成延迟：6.691 秒（一次端到端测量）
备份文件：knowledge-20260821T030826Z.db（9.6 MiB）
最近同步：首次建库，1,058 个新增 Chunk
```

## 已知外部限制

服务器到 `github.com` 的 HTTPS 连接在 15 秒测试中超时。首次部署使用已经受控克隆到服务器的
五个公开 checkout，并用 `sync apply --skip-git-fetch` 完成索引；常规定时同步保留 60 秒 Git
命令上限，远端不可达时会在 ingestion 前失败，继续服务旧索引。需要恢复服务器到 GitHub 的出站
网络（或由运维提供受信任的网络出口）后，定时 Git 更新才能真正获取新 revision。

除这一外部网络限制及未破坏生产 LLM 配置的故障演练外，部署、HTTPS、API、SSE、备份、恢复和
本地索引均已完成验证。
