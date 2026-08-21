# ZGLab Assistant Web（Phase 9C）

面向访客的 ZGLab Personal Knowledge Assistant Web UI。

## 技术栈

- Vue 3（Composition API）+ Vite + TypeScript
- 原生 / scoped CSS（无 UI 框架、无 Tailwind、无 Pinia）
- Vitest + Vue Test Utils（jsdom）

## 开发启动

前置：后端 API 在 `127.0.0.1:8000` 运行（见仓库根 README）。

```bash
cd web
npm install
npm run dev        # http://localhost:5173，/api 经 Vite proxy 转发到 8000
```

其他命令：

```bash
npm run test:run   # Vitest 单测（不连真实后端）
npm run build      # vue-tsc 类型检查 + 生产构建（dist/，已 gitignore）
```

## 配置

- `VITE_API_BASE_URL`：API base，默认空字符串（same-origin）。本地开发依赖
  Vite dev proxy（`/api → http://127.0.0.1:8000`）；Phase 10 生产为 Nginx
  同源部署。不要把 localhost 地址写进业务代码。见 `.env.example`。

## 目录

```text
web/
├── index.html
├── vite.config.ts          # dev proxy（SSE 流式透传）+ Vitest 配置
├── tsconfig.json
├── .env.example
├── src/
│   ├── main.ts
│   ├── App.vue             # 会话状态机（内存态，无持久化）
│   ├── api/
│   │   ├── contracts.ts    # 与后端窄契约对应的 TS 类型 + 文案映射
│   │   ├── client.ts       # fetch + ReadableStream SSE client
│   │   └── sse.ts          # 增量 SSE parser（跨 chunk 重组、heartbeat 忽略）
│   ├── components/
│   │   ├── AssistantHeader.vue
│   │   ├── ConversationView.vue
│   │   ├── QuestionComposer.vue
│   │   ├── AnswerCard.vue
│   │   ├── SourceList.vue
│   │   └── StatusIndicator.vue
│   └── styles/main.css     # CSS variables（颜色/间距/圆角）
└── tests/                  # sse / client / app / components 测试
```

## SSE client 说明

后端端点是 `POST /api/v1/ask/stream`，浏览器 `EventSource` 仅支持 GET，因此
客户端使用 `fetch + ReadableStream + TextDecoder` 加自实现的增量 SSE parser：

- parser 维护跨 chunk 行缓冲，事件可在任意字节边界被拆分；
- `event:` / `data:` / SSE comment（`: keep-alive` heartbeat，被忽略）；
- data 一律 `JSON.parse`，禁止 `eval`；解析失败转为受控前端错误，
  不把 raw payload 渲染到 DOM。

## 安全边界

- 所有渲染使用 Vue text binding，**不使用 v-html**：LLM 回答中的 HTML
  不会成为 XSS；回答按 `white-space: pre-wrap` 纯文本展示。
- 每次提问是独立请求：历史消息只存在于页面内存，不随请求发送、不写
  localStorage、无 Conversation Memory。
- 错误展示只使用前端映射文案 + request_id，不显示 stack trace、异常名
  或服务器原始响应体。
- 请求中禁止提交时重复发送（后端 production baseline concurrency=1）。
- AbortController 仅用于组件卸载时断开 fetch；HTTP disconnect 不等于
  后端 generation 取消（Phase 9B 冻结语义）。
