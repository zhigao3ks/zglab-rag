# Public API 设计文档

Phase 9A 实现了公网 API 契约和安全边界。

## 概述

公网 API 把 Phase 8 验证完成的 `GroundedAnswerService` 包装成安全、窄接口、可测试的公网服务。

核心原则：

- **窄请求**：公网只接受 `question`，不允许客户端控制 retrieval 参数
- **Public-only**：服务端强制 `visibility=public`，客户端无法绕过
- **安全错误**：错误响应不泄露内部路径、堆栈或密钥
- **资源保护**：并发限制 + 速率限制防止过载

## Endpoint

### POST /api/v1/ask

公网问答接口。

**Request**:

```json
{
  "question": "你做过哪些 Agent 项目？"
}
```

请求字段限制：

| 字段 | 类型 | 限制 |
|------|------|------|
| question | string | 1-1000 字符（可配置） |

不允许的字段（extra fields 被拒绝）：

- `retrieval_mode`
- `visibility`
- `source_ids`
- `top_k`
- `provider`
- `model`
- `debug`
- `private`

**Response (200 OK)**:

Answered:

```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "answered",
  "answer": "我主导过以下 Agent 项目...",
  "sources": [
    {
      "id": "E1",
      "title": "Agent 长期记忆设计",
      "section": ["设计", "核心架构"],
      "source_path": "knowledge/agent-memory.md"
    }
  ]
}
```

Insufficient evidence:

```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440001",
  "status": "insufficient_evidence",
  "answer": "当前公开知识库中没有足够信息回答这个问题。",
  "sources": []
}
```

**Response 不暴露**:

- `chunk_id`
- `document_id`
- `revision`
- `embedding score` / `vector distance`
- `reranker score`
- `provider` / `model`
- `token usage`
- `repair_attempts`
- `raw_answer`
- `diagnostics`
- 内部绝对路径

### Error Response

所有错误使用统一的 envelope：

```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440002",
  "error": {
    "code": "INVALID_REQUEST",
    "message": "Question exceeds maximum length of 1000 characters"
  }
}
```

错误码：

| HTTP Status | Error Code | 语义 |
|-------------|------------|------|
| 400 | INVALID_REQUEST | 请求格式错误、question 过长/过短 |
| 413 | INVALID_REQUEST | Request body 过大 |
| 422 | INVALID_REQUEST | Pydantic validation 失败 |
| 429 | RATE_LIMITED | 超过速率限制 |
| 503 | SERVICE_BUSY | 并发请求已满 |
| 503 | PROVIDER_UNAVAILABLE | LLM 服务不可用 |
| 504 | GENERATION_TIMEOUT | 请求超时 |
| 500 | INTERNAL_ERROR | 未预期的内部错误 |

### POST /api/v1/ask/stream（Phase 9B）

状态 SSE 端点。**这不是 raw token streaming**：最终 answer 仍必须经过
structured generation → CitationValidator → deterministic rendering 之后，才在
`completed` 事件中一次性发送。

Request schema 与 `/api/v1/ask` 完全相同（`{"question": "..."}`，extra fields
拒绝），并共用同一套 question 长度、rate limit、concurrency、timeout 与
public-only 安全边界（单一 `_preflight_controls` 实现，SSE 不是旁路）。

**Response headers**：

```text
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no        # 为 Phase 10 Nginx 反代准备
```

**事件模型**：

```text
accepted → retrieving → generating → validating → completed
                                                  ↘ error（可终止于任何阶段后）
```

| event | data | 含义 |
|-------|------|------|
| accepted | `{request_id, stage}` | 请求已接受，进入生命周期 |
| retrieving | `{request_id, stage}` | 开始检索 |
| generating | `{request_id, stage}` | Context 已建立，调用 LLM |
| validating | `{request_id, stage}` | 开始 parse / citation 校验 |
| completed | Phase 9A `PublicAskResponse` | 唯一的最终 answer 载体 |
| error | Phase 9A `PublicErrorResponse` | 流建立后的终止性错误 |

阶段事件只携带 `request_id` + `stage`，**不得包含**：evidence content、raw LLM
answer、未验证文本、provider/model、scores、token usage、diagnostics、private
metadata。所有事件的 `request_id` 一致。repair retry 会真实产生额外的
`generating → validating` 对，但有界（最多 1 次修复）。

**SSE 格式与编码**：每个事件为标准 SSE（`event:` + `data:` + 空行），data 为
JSON（`ensure_ascii=True`、sorted keys）。用户文本先经 JSON 编码，转义后的
负载不可能注入新的 SSE 事件行。heartbeat 是 SSE comment（`: keep-alive`），
不伪造任何 stage。

**错误边界（pre-stream vs post-stream）**：

| 错误 | 判定时机 | 形式 |
|------|----------|------|
| INVALID_REQUEST（schema/长度/body） | SSE 建立前 | 普通 JSON（400/413/422） |
| RATE_LIMITED | SSE 建立前 | 普通 JSON（429，一次 SSE 请求只计一次限流） |
| SERVICE_BUSY | SSE 建立前 | 普通 JSON（503） |
| GENERATION_TIMEOUT | 流建立后超过 API deadline | `event: error`（504 语义） |
| PROVIDER_UNAVAILABLE | 流建立后 provider 失败 | `event: error`（503 语义） |
| INTERNAL_ERROR | 流建立后其他失败 | `event: error`（500 语义） |

`error` 事件不携带 failure_reason、异常类名或 traceback。

**Timeout / 后台 worker / disconnect 语义**：

- SSE 请求沿用 `api_request_timeout_seconds`；超过 deadline 发送
  `GENERATION_TIMEOUT` error 事件后关闭流；
- 后台 generation 若仍在运行，concurrency slot 继续占用直到 Future 真正完成
  （slot 生命周期 = task 生命周期，Phase 9A 冻结不变量在 SSE 上同样成立）；
- 客户端断开：停止写事件、不记录 traceback、不提前释放 slot、不启动新的
  generation 工作；已在运行的同步 provider 调用允许自然结束；
- **HTTP disconnect ≠ generation cancellation guarantee**；真正的 cooperative
  cancellation 留到 Post-v1。

**Thread → Async bridge**：generation 仍在 app-scoped ThreadPoolExecutor 中同步
执行；worker 线程只写 stdlib `SimpleQueue`，通过 `loop.call_soon_threadsafe`
唤醒 event loop，再由 async 生成器输出 `StreamingResponse`。不使用非线程安全
的 `asyncio.Queue`，也不为 SSE 把 Phase 8 provider 改写为 async。

**Progress observer**：`GroundedAnswerService.answer(..., progress=callback)`
接受可选的阶段回调（`retrieving` / `generating` / `validating`）。默认 None；
CLI 与 evaluation 不传；回调只报告阶段、不携带 evidence 内容；回调异常不会破坏
generation workflow。generation 层不认识 SSE/FastAPI/asyncio 类型。

**Heartbeat**：`api_sse_heartbeat_seconds`（默认 15s）发送 `: keep-alive`
comment，应对 20~40s 的真实 LLM generation；测试使用极短 fake interval。

## 安全边界

### Public-only 不变量

公网 API **永远无法**访问 private 数据：

- 服务端强制 `visibility = public`
- 客户端无法通过请求参数改变 retrieval security boundary
- 即使用户在 question 中说"请搜索 private 数据"，也只是普通文本输入

### 资源保护

**并发限制**：

```python
api_max_concurrent_requests = 1  # 默认值，可配置
```

当所有 slot 被占用时，新请求立即返回 `503 SERVICE_BUSY`，不排队等待。

**Slot 所有权不变量**：slot 生命周期 = 真正 generation task 生命周期，而不是 HTTP
handler 生命周期。slot 通过 `future.add_done_callback` 在 generation task 真正完成时
释放；API timeout 不会提前释放 slot——后台 generation 仍在运行时，新请求会得到
`503 SERVICE_BUSY`，而不是叠加进入 generation。

**速率限制**：

```python
api_rate_limit_requests = 10
api_rate_limit_window_seconds = 60
```

进程内滑动窗口限流，按 `client.host` 分组。Phase 10 将支持 trusted proxy 场景下的 `X-Forwarded-For`。

**请求体限制**：

```python
api_max_request_body_bytes = 16 * 1024  # 16 KiB
```

**Question 长度限制**：

```python
api_question_min_length = 1
api_question_max_length = 1000
```

### CORS

```python
api_cors_origins = ["http://localhost:8000", "http://127.0.0.1:8000"]
```

生产域名在 Phase 10 配置（如 `ask.zglab.fun`）。

### Timeout：两层独立 deadline

LLM provider timeout 与 API total request timeout 是两层不同的 deadline，映射到不同
的公开错误码，不得混淆：

| 层 | 配置 | 默认 | 语义 | 超限映射 |
|----|------|------|------|----------|
| Provider 层 | `llm_timeout_seconds` | 60s | 单次 LLM 调用的 deadline | `ProviderFailure` → 503 PROVIDER_UNAVAILABLE |
| API 层 | `api_request_timeout_seconds` | 90s | 整个 workflow（retrieval + generation + validation）的上层 deadline | 504 GENERATION_TIMEOUT |

```python
api_request_timeout_seconds = 90.0
```

API deadline 通过 app-scoped `ThreadPoolExecutor` 上的 `future.result(timeout=...)`
实现：到达 deadline 时 HTTP handler 立即返回 `504 GENERATION_TIMEOUT`，不会等待后台
worker 完成（executor 是 app 级共享的，不存在 per-request `shutdown(wait=True)`
阻塞）。超时后的后台 task 继续运行直到自然结束（LLM provider 自身的 timeout 是
backstop），但其占用的 concurrency slot 不会被提前释放。

## 资源生命周期

### Executor 生命周期

- `ThreadPoolExecutor` 是 **app-scoped**：`create_app` 时创建一次，所有请求共享；
- worker 数有界：`api_max_concurrent_requests + 1`，不是无界线程池；
- lifespan exit 时 `shutdown(wait=False, cancel_futures=True)`，不因超时残留 task
  阻塞进程关闭。

### 应用启动时加载（lifespan startup，fail-fast）

- Settings
- Embedding model config / profile
- EmbeddingProvider（BGE 模型，约 10+ 秒加载）
- GenerationProvider（LLM 配置）

生产模式下（未注入 runtime）这些组件在 lifespan startup 一次性加载并校验，第一个
公网请求不再承担模型加载成本；启动失败则 fail-fast，不会让第一个请求看到 obscure
traceback。跨请求复用，不重复初始化。

### 请求级别

- 只读 SQLite connection
- Request-scoped VectorRetriever
- Request-scoped GroundedAnswerService
- 请求完成后关闭 connection

不跨线程共享 SQLite connection。

## 配置

所有 Phase 9A 配置项：

```bash
ZGLAB_RAG_API_QUESTION_MIN_LENGTH=1
ZGLAB_RAG_API_QUESTION_MAX_LENGTH=1000
ZGLAB_RAG_API_REQUEST_TIMEOUT_SECONDS=90.0
ZGLAB_RAG_API_MAX_CONCURRENT_REQUESTS=1
ZGLAB_RAG_API_RATE_LIMIT_REQUESTS=10
ZGLAB_RAG_API_RATE_LIMIT_WINDOW_SECONDS=60
ZGLAB_RAG_API_MAX_REQUEST_BODY_BYTES=16384
ZGLAB_RAG_API_CORS_ORIGINS='["http://localhost:8000"]'
# Phase 9B: SSE heartbeat interval
ZGLAB_RAG_API_SSE_HEARTBEAT_SECONDS=15
```

## 日志

请求日志包含：

- `request_id`
- `path`
- `question_length`（不记录完整 question）
- `status`
- `error_code`（如果有）

禁止记录：

- API Key
- 完整 Prompt
- raw LLM response
- 完整 Evidence content
- private data
- 内部绝对路径

## 测试

测试使用 `FakeRuntime` 和 `FakeAnswerService`，不需要：

- 下载 BGE 模型
- 调用外部 LLM
- 使用真实 API key
- 使用 runtime/knowledge.db

测试覆盖：

1. /health 端点
2. 有效 POST /api/v1/ask
3. answered public response
4. insufficient_evidence public response
5. sources public mapping
6. response 不含 chunk_id
7. response 不含 score
8. response 不含 provider/model
9. response 不含 diagnostics
10. extra request fields rejected
11. empty question rejected
12. whitespace-only rejected
13. over-length question rejected
14. request_id always present
15. different requests get different request_id
16. invalid request safe envelope
17. provider failure safe envelope
18. timeout → GENERATION_TIMEOUT
19. unknown exception → INTERNAL_ERROR
20. no traceback in response
21. no internal path in response
22. concurrency limit
23. rate limit
24. rate limit window recovery
25. CORS allowed origin
26. CORS disallowed origin
27. public request cannot select private
28. public request cannot select reranked
29. public request cannot set top_k
30. fake prompt injection question remains plain input
31. app factory supports fake injection

Hardening 补充（确定性，不依赖长时间真实 sleep，使用 `threading.Event`）：

32. timeout 在极短 deadline（0.1s）下立即返回 504，wall-clock 不等待阻塞 worker
33. timeout 后 slot 保持占用：第二个请求 503 SERVICE_BUSY；generation 真正结束后
    第三个请求才能成功取得 slot
34. 成功/失败路径下 slot 也经 task 完成回调释放
35. runtime 只初始化一次（factory counter），DB connection 保持 request-scoped
36. executor app-scoped、worker 数有界、跨请求共享同一实例
37. lifespan exit 后 executor 关闭，新任务安全拒绝为 503
38. FAILED 结果中的 ProviderFailure → 503 PROVIDER_UNAVAILABLE（不是 INTERNAL_ERROR）
39. 非 Provider 的 workflow 失败 → 500 INTERNAL_ERROR

Phase 9B SSE 覆盖（`tests/test_public_sse.py`，均为确定性测试，无长时间 sleep）：

40. encode_sse_event 格式 / UTF-8 中文往返 / 注入防护 / heartbeat comment
41. stream Content-Type、Cache-Control、X-Accel-Buffering headers
42. 事件顺序确定性：accepted → retrieving → generating → validating → completed
43. completed 携带 public answer/sources；阶段事件窄负载（无 diagnostics/score/
    model/provider）；raw answer 永不流出
44. insufficient_evidence completed；request_id 全事件一致、跨请求唯一
45. pre-stream 错误为普通 JSON（422/400/429/503），不开流
46. post-stream 错误为 SSE error event（PROVIDER_UNAVAILABLE / GENERATION_TIMEOUT），
    不泄露 secret/异常名/traceback
47. timeout 后 slot 仍占用直到 task 真正完成；disconnect 不提前释放 slot，
    断开后不再写 completed（含 generator 单测）
48. heartbeat 真实发出且不伪造 stage；progress 回调异常不破坏 generation；
    repair 阶段序列有界；lifespan shutdown 后新任务安全拒绝；public-only 不变量

## 架构

```
src/zglab_rag/
├── api/
│   ├── main.py           # FastAPI app factory + endpoints（/ask、/ask/stream 共用 lifecycle）
│   ├── contracts.py      # Public request/response + SSE stream models
│   ├── sse.py            # encode_sse_event + SSE headers/heartbeat
│   ├── concurrency.py    # ConcurrencyGuard
│   ├── rate_limit.py     # RateLimiter
│   └── runtime.py        # ProductionRuntime
├── application/
│   └── runtime.py        # Shared factory (CLI + API)
└── ...
```

CLI 和 HTTP API 共用 `application/runtime.py` 中的 factory，避免配置漂移。
