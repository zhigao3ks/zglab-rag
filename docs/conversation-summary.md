# Conversation Summary（Phase 15C）

`conversation.db` schema v2 增加 `conversation_summaries`：每个 conversation 最多一条摘要，包含 `content`、`covered_through_message_id` 与时间戳。v1 在单个 SQLite 事务中迁移到 v2；未来版本 fail closed。摘要随 conversation 删除级联删除，repository 在每次读取与写入时验证 owner、coverage message 属于同一 conversation 且 role 为 ASSISTANT，并禁止 coverage 回退。

摘要是增量的：最近 4 个完整 turn 始终保持 raw；只有至少积累 4 个尚未覆盖的较早完整 turn 时，才处理最早的有界 batch（最多 8 个），并把 coverage 向前推进。不会重新总结整个 conversation，也不会加载无界 message history。

摘要使用现有 `GenerationProvider` 的 `GenerationRequest`，`allowed_evidence_ids=()`。prompt 将 history 明确称为 untrusted data，要求仅输出 `{"summary":"..."}`，并禁止执行历史中的指令、补充事实、提升旧 assistant 回答的事实地位或伪造引用。无效 JSON、空结果、provider failure、删除竞态与 repository error 全部 fail-soft：保留旧摘要且不影响用户请求。

`ZGLAB_RAG_CONVERSATION_SUMMARY_ENABLED` 默认 `false`，代码合并不会增加生产 LLM 成本。开启后，成功持久化 ASSISTANT turn 才会非阻塞调度 refresh；一个独立单 worker 最多同时处理一个任务，忙时直接丢弃额外任务而不建立无界队列。它不消耗 ask quota、不产生 SSE progress，也不会使用核心请求 executor。
