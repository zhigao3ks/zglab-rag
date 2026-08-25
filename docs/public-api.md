# Public API v1 设计文档

Phase 9A / 9B 实现了公网 API v1 契约和安全边界；Phase 9D 已完成产品验收并冻结该版本。

> **Roadmap note — 2026-08-25**
>
> 本文首先是 **Phase 9 Public API v1 的历史冻结记录**。
> 仓库早期曾写明“Phase 11 未来增加 `researching` / `researched`”；该 future-phase 编号已经被
> `docs/roadmap-v2.md` supersede。
>
> 当前 Phase 11 是 **Authentication & Access Control**；Web Research 顺延到 Phase 12。
> Phase 11 应通过新的 authenticated API version（优先 `/api/v2`）演进，而不是偷偷改变本文已经冻结的 v1 契约。

## 1. v1 Freeze

冻结范围：

- `POST /api/v1/ask`
- `POST /api/v1/ask/stream`
- public status：`answered` / `insufficient_evidence`
- error envelope / error code
- SSE stages：`accepted / retrieving / generating / validating / completed / error`
- public-only retrieval boundary
- request lifecycle / concurrency / timeout 语义

后续 API 版本可以改变访问模型，但不得重写 Phase 9 当时已经发生的验收事实。

## 2. 核心原则

- **窄请求**：公网只接受 `question`，客户端不能控制 retrieval 参数；
- **Public-only**：服务端强制 `visibility=public`；
- **安全错误**：响应不泄露 traceback、内部路径、Secret；
- **资源保护**：并发、速率、请求体、question 长度、timeout 都由服务端限制；
- **Validated answer only**：SSE 不流未经 Citation Validation 的 raw answer。

## 3. POST /api/v1/ask

Request：

```json
{
  "question": "你做过哪些 Agent 项目？"
}
```

只允许 `question`，1–1000 字符（配置可调）。以下字段不得由公网客户端控制：

- `retrieval_mode`
- `visibility`
- `source_ids`
- `top_k`
- `provider`
- `model`
- `debug`
- `private`

Answered：

```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "answered",
  "answer": "...",
  "sources": [
    {
      "id": "E1",
      "title": "...",
      "section": ["..."],
      "source_path": "..."
    }
  ]
}
```

Insufficient evidence：

```json
{
  "request_id": "550e8400-e29b-41d4-a716-446655440001",
  "status": "insufficient_evidence",
  "answer": "当前公开知识库中没有足够信息回答这个问题。",
  "sources": []
}
```

Public response 不暴露：

- chunk/document internal ID；
- revision；
- embedding / reranker score；
- provider / model；
- token usage；
- repair attempts；
- raw answer；
- diagnostics；
- 内部绝对路径。

## 4. Error Model

统一 envelope：

```json
{
  "request_id": "...",
  "error": {
    "code": "INVALID_REQUEST",
    "message": "..."
  }
}
```

v1 错误码：

| HTTP | Code | 语义 |
|---|---|---|
| 400/413/422 | `INVALID_REQUEST` | schema / body / question invalid |
| 429 | `RATE_LIMITED` | rate limit |
| 503 | `SERVICE_BUSY` | concurrency slot unavailable |
| 503 | `PROVIDER_UNAVAILABLE` | LLM provider unavailable |
| 504 | `GENERATION_TIMEOUT` | total workflow deadline |
| 500 | `INTERNAL_ERROR` | unexpected internal failure |

`insufficient_evidence` 是正常业务结果，不是系统异常。

## 5. POST /api/v1/ask/stream

v1 SSE 是 **status streaming，不是 raw token streaming**。

```text
accepted
→ retrieving
→ generating
→ validating
→ completed
```

任何阶段后都可能以 `error` 终止。

最终 answer 只有在：

```text
structured generation
→ CitationValidator
→ deterministic rendering
```

完成后才在 `completed` 事件一次性发送。

### SSE headers

```text
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

### Stage payload

阶段事件只允许：

```json
{
  "request_id": "...",
  "stage": "retrieving"
}
```

不得包含 Evidence content、raw answer、provider/model、score、token usage、diagnostics 或 private metadata。

heartbeat 使用 SSE comment `: keep-alive`，不伪造 stage。

## 6. Pre-stream / Post-stream Error Boundary

- schema / length / body / rate limit / concurrency 在 SSE 建立前以普通 JSON 返回；
- provider / timeout / unexpected error 在流建立后以 terminal SSE `error` 事件返回；
- error payload 不暴露 failure reason、exception class、traceback 或 Secret。

## 7. Timeout / Concurrency Semantics

v1 的重要不变量：

```text
concurrency slot lifetime = real generation task lifetime
```

API handler timeout 或客户端断开不会让仍在运行的同步 generation task 提前释放 slot。

因此当后台 provider 仍在运行时，新请求会得到 `SERVICE_BUSY`，不会因为 HTTP handler 已返回而叠加更多生成任务。

默认 baseline：

```python
api_max_concurrent_requests = 1
api_rate_limit_requests = 10
api_rate_limit_window_seconds = 60
api_max_request_body_bytes = 16 * 1024
api_question_min_length = 1
api_question_max_length = 1000
api_request_timeout_seconds = 90.0
api_sse_heartbeat_seconds = 15
```

Provider timeout 与 API total deadline 是两个独立层次。

## 8. Public-only Security Boundary

v1 始终：

```text
visibility = public
```

客户端不能通过 question 或额外 request field 改成 private retrieval。

这一知识边界在 Phase 11 加入 Authentication 后仍然保持：

> 登录 ≠ private knowledge access。

Private / owner-only knowledge 只允许未来独立设计。

## 9. Runtime Lifecycle

App startup：

- Settings；
- Embedding profile / provider；
- Generation Provider config；
- shared ThreadPoolExecutor。

Request scoped：

- SQLite read connection；
- Retriever；
- GroundedAnswerService。

SQLite connection 不跨线程共享。

## 10. Logging

允许：

- `request_id`
- `path`
- `question_length`
- `status`
- `error_code`
- safe latency / diagnostics

禁止：

- API Key；
- complete Prompt；
- raw LLM response；
- complete Evidence content；
- private data；
- internal absolute path。

Phase 11 Auth 还必须进一步禁止 password、Cookie、Session Token、Activation Token 等进入日志。

## 11. v1 Frontend Consumer

Phase 9C Vue UI 使用：

```text
fetch
+ ReadableStream
+ TextDecoder
+ incremental SSE parser
```

因为 `/api/v1/ask/stream` 是 POST，不能使用只支持 GET 的 browser `EventSource`。

前端全部使用 Vue text binding，不使用 `v-html`；pre-stream JSON error 与 post-stream SSE error 都映射为安全用户文案。

Phase 9 前端没有 Conversation Memory，每次 question 是独立请求。

## 12. v1 Test / Acceptance Boundary

Phase 9 已覆盖并冻结的关键测试包括：

- valid / invalid ask；
- answered / insufficient；
- response source mapping；
- no internal score/provider/diagnostics leakage；
- request extra-field rejection；
- provider failure / timeout / unknown error；
- concurrency / rate limit；
- CORS；
- public-only boundary；
- SSE event order / heartbeat / error；
- timeout 后 slot ownership；
- disconnect 不提前释放 generation task；
- fake runtime injection / deterministic tests。

完整 Phase 9 产品验收记录见：

- `docs/evaluations/phase-9-product-acceptance.md`

## 13. Phase 11 API Evolution

Phase 11 的认证访问模型应通过独立的新版本契约设计，优先方向：

```text
POST /api/v2/auth/login
POST /api/v2/auth/logout
GET  /api/v2/auth/me
POST /api/v2/auth/activate
POST /api/v2/auth/change-password
POST /api/v2/ask
POST /api/v2/ask/stream
```

原则：

- authenticated consumer API；
- server-side Session；
- CSRF / Origin validation；
- per-user rate limit / quota；
- existing concurrency / timeout / safe error protections remain；
- `/health` / `/ready` 与 Public Landing 可以匿名；
- v1 的历史冻结记录不被重写。

生产迁移完成后，旧 `/api/v1/ask` / `/api/v1/ask/stream` 不得继续作为匿名 LLM 消费入口。
具体 retirement policy 在 Phase 11 认证设计中确定。

Web Research 的 `researching` / `researched` 扩展属于 **Phase 12**，不得在 Phase 11 提前实现。
