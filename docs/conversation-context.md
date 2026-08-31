# Phase 15B — Multi-turn Context

`conversation_id` 仅由 authenticated principal 与服务端 `conversation.db` 共同解析。客户端不能上传 history、role、owner 或 assembled prompt。

## 有界装配

默认限制：最多 4 个完整轮次、6000 个字符、每条消息 2000 个字符。装配器只接受稳定排序的同一会话消息，丢弃 dangling USER 等不完整轮次；从最新完整轮次向前选择，再按时间顺序渲染。当前问题在 context snapshot 后才持久化，因此不会重复出现。

`conversation_id=null` 不加载 context，保持原有单轮调用路径。

## 信任边界

Conversation Context 是低信任的 reference-resolution data：可帮助 Personal retrieval、Web search 与 grounded generation 理解指代；它不是 Evidence、citation/source、system instruction 或 Agent plan。所有事实仍只能由本轮 Personal retrieval 或本轮 Web research evidence 支撑。

历史文本不能改变 capability selection、Auth、quota、concurrency、Agent step budget 或 MCP allowlist。不会通过公开 API、SSE、日志或错误响应输出其正文；日志只记录 turn/char/truncated 计数。

本阶段不含 summary、semantic history retrieval、embedding/vector index、Web/retrieval evidence reuse、tool-result reuse、cache 或 long-term memory；这些属于 15C/15D 及后续阶段。
