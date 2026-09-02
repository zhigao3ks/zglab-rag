# Source Acquisition & Gitee Mirror Runtime — 生产验收（2026-09-02）

## 结论

> **PASS — Source Acquisition & Gitee Mirror Runtime production accepted**

生产应用 revision：

```text
cdcc8a84b130aa1b56047bc8eed53091184d91cf
```

## Managed checkout

Managed root：`/opt/zglab-rag/sources`，权限为 `zglab:zglab 0750`。

| Source | Gitee managed revision |
| --- | --- |
| notes | `6d9ee6e32dc55d648f5b9c1bd8fad8d3b562bde7` |
| zglab-website | `0f5e4de6e2fa4d9349d839e4fef313626e9bda3e` |
| resume-tailor-agent | `019e7928fd36ea800453535b6ff1eb9792fb398b` |
| zglab-tools | `03796f6f7ec0f40ad24183dc9f25e2732a4e63c7` |
| zglab-daily | `d91a5f3341d6f42c62a76c8c95ec2cc78f4ada5b` |

所有 checkout 的 origin 均为对应 `https://gitee.com/Zg443/<repository>.git`，工作树 clean，
apply 后 source status 均为 unchanged。

## 同步与索引

正式 apply 前 plan：

```text
1203 chunks
new=157
changed=0
removed=12
unchanged=1046
```

成功 run：`02c77bbb-5f4d-4f2b-b403-5b4efb3d3497`，embedded `157` chunks，耗时 `21.466s`。
最终 index 为 1203 chunks，所有 managed revision 均已追上。

pre-apply backup：

```text
/opt/zglab-rag/runtime/backups/knowledge-20260902T070528Z.db
```

Embedding 使用 `BAAI/bge-small-zh-v1.5` 的 512-dim contextual profile。

## 发现与处理

首次 apply 在 embedding 阶段失败：sync unit 缺少 `HF_HOME` 与 `HF_HUB_CACHE`，因此在
`HF_HUB_OFFLINE=1` 时未看到预置的模型 cache。确认
`/opt/zglab-rag/models/huggingface/hub` 已存在对应 snapshot 后，为 sync unit 补齐与 API 一致的
HF cache 环境；离线加载测试及后续 apply 成功。

部署 dry-run 同时确认 rsync 必须保护 `runtime/`、`.git/`、`.venv/`、`web/dist/` 等非部署数据。
生产配置显式设置：

```text
ZGLAB_RAG_CONVERSATION_DATABASE_PATH=/opt/zglab-rag/runtime/conversation.db
```

从而避免持久 DB 依赖相对 `runtime/...` 路径。

## 最终检查

```text
API active
sync timer active
backup timer active
/health OK
/ready OK
knowledge.db PRAGMA integrity_check = ok
```

`legacy app/runtime/auth.db` 与旧 source checkouts 均保留；本 Maintenance Track 未进行清理。
