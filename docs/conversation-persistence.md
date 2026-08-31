# Phase 15A — Conversation Persistence

Phase 15A1 建立 framework-free domain 与独立 SQLite storage；15A2 在此基础上接入 authenticated Conversation API 和可选 ask persistence。当前仍不加载历史进入生成上下文，也没有 UI/Sidebar。

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

## 15A2 API contract

全部 endpoint 需要 authenticated session：

```text
POST   /api/v2/conversations
GET    /api/v2/conversations
GET    /api/v2/conversations/{id}
PATCH  /api/v2/conversations/{id}
DELETE /api/v2/conversations/{id}
GET    /api/v2/conversations/{id}/messages
```

- POST / PATCH / DELETE 同时要求既有 Origin 与 CSRF 校验；
- title 会 trim，并限制为 1–120 字符；不使用 LLM 自动生成；
- 所有操作按 authenticated principal 的 `user_id` 约束；非 owner 与不存在资源统一返回同一 404，避免 IDOR / existence leak；
- message history 仅通过 owner-scoped read endpoint 返回，不接受客户端指定 role、owner 或 message content 来伪造 ask 流程。

## Ask persistence

`POST /api/v2/ask` 和 `POST /api/v2/ask/stream` additive 支持：

```text
conversation_id: int | null
```

- 缺省或 null 时完全保持既有独立请求行为，不自动创建会话；
- 有值时先以 authenticated principal 验证 owner；
- 通过 question、policy、concurrency、quota 等现有 admission 后，持久化 USER message；
- 只有公开终态 `answered` / `insufficient_evidence` 才持久化 ASSISTANT message；
- 技术错误、安全拒绝、认证/CSRF、quota、validation 等失败不会生成 ASSISTANT message，也不会在 admission 前写 USER message；
- 普通 ask 与 SSE 复用相同的 terminal-result persistence helper。

最重要的是：`conversation_id` 只表示 ownership + persistence。repository history **不会**被读取到 retrieval、prompt、capability 或 Agent context。

## 明确不在 15A 范围内

- Assistant Sidebar、前端 history 或 session 恢复；
- multi-turn prompt/context、summary、compression 或任何 evidence/tool reuse；
- Redis 与 long-term memory。
