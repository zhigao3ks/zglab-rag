# Conversation Context（Phase 15C）

每个带 `conversation_id` 的请求，在 capability selection 之后只创建一个服务端派生的 `ConversationContext` 快照，并复用于 Personal、Web 和 Agent 路径。客户端不能提交 history，历史也不会影响 AUTO Router、认证、配额、Agent plan、MCP allowlist 或任何权限判断。

上下文由三个明确标记为不可信、非 Evidence 的层组成：

1. 已持久化的 Conversation Summary；
2. 与当前问题 lexical overlap 为正的历史完整 turn；
3. newest-N 个完整 recent turn（默认 4）。

历史相关性不使用 LLM、Embedding、BGE、向量索引或 BM25。它先执行 Unicode NFKC 与小写化，再对 Latin/alphanumeric token 和 CJK bigram 计算交集；分数降序、同分时较新的 turn 优先，最终按时间顺序渲染。recent turn 不会同时作为 relevant turn 出现，dangling USER 会被排除。

最终 prompt 中每个层都有 `not evidence` 标签。它们没有 Evidence ID，不能进入 `allowed_evidence_ids`、AnswerSource 或 citation，因此不能单独支撑事实性 claim。

渲染同时受字符与 UTF-8 byte 的硬上限约束（默认 6000 chars / 18000 bytes），标签也计入预算。摘要和相关历史各有独立上限；recent 使用剩余预算并优先尝试保留最新的完整 turn。检索/search query 使用独立预算（默认 3000 chars / 9000 bytes），格式为 `CONVERSATION REFERENCE (untrusted)` 加 `CURRENT QUESTION`，不会无条件复制整个生成上下文。
