# Phase 15A1 — Conversation Domain & Persistence Foundation

Phase 15A1 只建立 Conversation / Message 的 framework-free domain 与独立 SQLite storage，不改变现有 API、SSE、Assistant UI 或 ask / Agent runtime。

## 独立生命周期

运行时路径由 `conversation_database_path` 配置，默认值为：

```text
runtime/conversation.db
```

它独立于 `auth.db` 与 `knowledge.db`：

- `auth.db` 持有用户、session、安全事件与 quota；
- `knowledge.db` 持有公开知识索引；
- `conversation.db` 只持有会话元数据与消息正文。

`owner_user_id` 是由未来 authenticated principal 传给 repository 的 ownership boundary。由于 `conversation.db` 和 `auth.db` 有独立 SQLite lifecycle，二者之间**没有跨数据库 foreign key**。

## Schema v1

```text
conversations
├── id
├── owner_user_id
├── title
├── created_at
└── updated_at

messages
├── id
├── conversation_id → conversations.id (ON DELETE CASCADE)
├── role: USER | ASSISTANT
├── content
└── created_at
```

数据库使用 WAL、`foreign_keys=ON`、`schema_metadata.schema_version=1`。空库可以显式初始化；非 ZGLab 数据库、缺少 schema version 或版本不匹配都会 fail-fast。

## Repository boundary

- conversation 读取、列举、标题修改、删除都要求 `owner_user_id`；
- message 追加与读取同样要求 `owner_user_id`；
- message append 在一个事务中确认 ownership、写入 message 并更新 conversation 的 `updated_at`；
- 会话列表按 `updated_at DESC, id DESC`；消息历史按 `created_at ASC, id ASC`，因此顺序稳定；
- repository 返回 dataclass domain objects，从不泄漏 `sqlite3.Row`。

本阶段不写入 Prompt、Agent trace、Web 页面正文、Tool arguments、内部 reasoning 或 temporary evidence。

## 明确不在 15A1 范围内

- `/api/v2/conversations` 或任何 API/SSE contract 修改；
- `conversation_id` 进入 ask 请求；
- Assistant Sidebar、前端 history 或 session 恢复；
- multi-turn prompt/context、summary、compression 或任何 evidence/tool reuse；
- Redis 与 long-term memory。
