# ZGLab Assistant Web（Phase 9C → Phase 11）

面向访客的 ZGLab Personal Knowledge Assistant Web UI。Phase 11 起升级为
Public Landing + Login + Activation + Authenticated Assistant。

## 技术栈

- Vue 3（Composition API）+ Vue Router + Vite + TypeScript
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
│   ├── App.vue             # 会话恢复（GET /auth/me）+ <router-view>
│   ├── router.ts           # 路由与 UX 守卫（真正授权在后端）
│   ├── auth/
│   │   ├── api.ts          # /api/v2/auth REST client（same-origin + CSRF 头）
│   │   └── store.ts        # 内存态 auth 状态（不落 localStorage）
│   ├── api/
│   │   ├── contracts.ts    # 与后端窄契约对应的 TS 类型 + 文案映射
│   │   ├── client.ts       # fetch + ReadableStream SSE client（/api/v2）
│   │   └── sse.ts          # 增量 SSE parser（跨 chunk 重组、heartbeat 忽略）
│   ├── views/
│   │   ├── LandingView.vue     # 公开落地页（匿名）
│   │   ├── LoginView.vue
│   │   ├── ActivateView.vue    # /activate#token=... 设置密码（fragment transport）
│   │   └── AssistantView.vue   # 认证后助手（含退出/改密）
│   ├── conversation/types.ts   # 会话 view-model 类型
│   ├── components/
│   │   ├── AssistantHeader.vue
│   │   ├── ConversationView.vue
│   │   ├── QuestionComposer.vue
│   │   ├── AnswerCard.vue
│   │   ├── SourceList.vue
│   │   └── StatusIndicator.vue
│   └── styles/main.css     # CSS variables（颜色/间距/圆角）
└── tests/                  # sse / client / app / components / auth 测试
```

## 认证（Phase 11）

- 会话凭证是 HttpOnly Cookie（`__Host-zglab_session`），JS 永远读不到；
  前端用 `credentials: "same-origin"` 自动携带。
- CSRF token 由 `/api/v2/auth/login` / `/api/v2/auth/me` 下发，保存在内存
  store，随 state-changing 请求以 `X-CSRF-Token` 头发送；**不存
  localStorage**。
- 路由守卫只负责 UX；任何能力调用都以后端 AuthN/AuthZ 为准。
- 刷新页面后通过 `GET /api/v2/auth/me` 恢复登录态。

## SSE client 说明

后端端点是 `POST /api/v2/ask/stream`（Phase 11；v1 已退役），浏览器
`EventSource` 仅支持 GET，因此
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

## 契约稳定性

Phase 9D 全系统产品验收通过后，后端 Public API v1（端点、status、错误码、
SSE stages）曾冻结；Phase 11 引入认证后的 `/api/v2`，v1 通过
`ZGLAB_RAG_API_V1_RETIRED` 退役（410）。v2 的 SSE stages / status / 错误
信封与 v1 保持一致，仅新增安全错误码（见仓库根 `docs/api-v2.md`）。验收记录见
仓库根 `docs/evaluations/phase-9-product-acceptance.md` 与
`docs/evaluations/phase-11-authentication-acceptance.md`。
