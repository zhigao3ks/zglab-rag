# ZGLab Assistant Web

`web/` 是 `ask.zglab.fun` 的 Vue 3 前端。

当前产品形态：

```text
Public Landing
+ Login / Activation
+ Authenticated Assistant
+ Auto / Personal / Web / Agent modes
+ SSE status updates
```

Phase 14 已完成 Agent 产品接入并生产封板；UX Track 已完成。下一 Product Phase 是 Phase 15 Session Memory，但尚未开始。

## 技术栈

- Vue 3（Composition API）
- Vue Router
- Vite
- TypeScript
- 原生 / scoped CSS
- Vitest + Vue Test Utils（jsdom）

无 Tailwind、无 Pinia、无 UI framework。

## 开发启动

前置：后端 API 在 `127.0.0.1:8000` 运行。

```bash
cd web
npm install
npm run dev
```

Vite 开发服务器默认：

```text
http://localhost:5173
/api → http://127.0.0.1:8000
```

测试与构建：

```bash
npm run test:run
npm run build
```

## 当前目录

```text
web/
├── index.html
├── vite.config.ts
├── tsconfig.json
├── src/
│   ├── main.ts
│   ├── App.vue
│   ├── router.ts
│   ├── auth/
│   │   ├── api.ts
│   │   └── store.ts
│   ├── api/
│   │   ├── contracts.ts
│   │   ├── client.ts
│   │   └── sse.ts
│   ├── views/
│   │   ├── LandingView.vue
│   │   ├── LoginView.vue
│   │   ├── ActivateView.vue
│   │   └── AssistantView.vue
│   ├── conversation/
│   │   ├── types.ts
│   │   └── useConversationScroll.ts
│   ├── components/
│   │   ├── AssistantHeader.vue
│   │   ├── ConversationView.vue
│   │   ├── QuestionComposer.vue
│   │   ├── AnswerCard.vue
│   │   ├── SourceList.vue
│   │   └── StatusIndicator.vue
│   └── styles/
│       └── main.css
└── tests/
    ├── app.test.ts
    ├── auth.test.ts
    ├── client.test.ts
    ├── components.test.ts
    ├── conversation-scroll.test.ts
    └── sse.test.ts
```

## Authentication

- Session credential 是 HttpOnly Cookie，前端 JS 不读取 session token；
- 请求通过 `credentials: "same-origin"` 自动携带 Cookie；
- CSRF token 保存在内存 auth store，不写 localStorage；
- 刷新后通过 `GET /api/v2/auth/me` 恢复状态；
- route guard 只负责 UX，真正 AuthN/AuthZ 由后端强制；
- 没有 public signup。

## Ask Modes

Composer 当前支持：

```text
auto
personal
web
agent
```

这些只表示产品 mode，不代表客户端拥有 capability 授权。
服务器会重新执行 policy、quota、concurrency 与 kill switch 校验。

## SSE Client

后端使用 `POST /api/v2/ask/stream`，因此前端采用：

```text
fetch
+ ReadableStream
+ TextDecoder
+ incremental SSE parser
```

而不是浏览器 `EventSource`。

Personal stages：

```text
accepted → retrieving → generating → validating → completed
```

Web stages：

```text
accepted → researching → generating → validating → completed
```

Agent stages：

```text
accepted → planning → executing → synthesizing → validating → completed
```

前端只渲染公开 stage；Agent plan、observation、tool raw data、网页正文与内部推理不会通过 SSE 下发。

## 当前 Conversation 语义

当前页面中的 `messages` 只是本地 view-model：

- 历史消息只存在当前页面内存；
- 下一次请求只发送当前 question + mode；
- 不发送历史消息；
- 不写 localStorage；
- 不存在 conversation_id；
- 不存在后端 Conversation / Message persistence；
- 不存在 Session Memory。

这些属于 Phase 15，当前 UX Track 不得提前实现。

## Assistant 布局

`AssistantView.vue` 使用受 viewport 约束的应用 shell：

```text
account bar
password form（optional）
AssistantHeader
app-main
  ├── Chat Area
  │   └── Message Scroller
  └── Composer Dock
footer
```

`app-shell` 固定为 `100dvh`，Header、account controls、Composer Dock 和 footer 均不参与消息滚动；只有 Message Scroller 使用 `overflow-y: auto`。窄屏时 account controls 会换行，composer controls 重排，textarea 有 viewport-relative max-height 并在内部滚动。长答案、来源和用户消息使用换行规则，避免横向溢出。

未来的 Session Sidebar 仅保留布局演进空间，当前没有 Sidebar 数据或功能。

## Conversation Scroll State

```text
Assistant Layout
├── future Session Sidebar slot
└── Workspace
    ├── Header / Navigation
    └── Chat Area
        ├── independent Message Scroll Area
        └── Composer Dock
```

```text
FOLLOWING
↕
DETACHED
```

- send：主动恢复 FOLLOWING 并定位最新；
- 初始为 `FOLLOWING`；near-bottom threshold 为 96px；
- send：主动恢复 FOLLOWING 并在 DOM 更新后定位最新；
- FOLLOWING：SSE stage / completed / error 更新后继续跟随；
- user scrolls up：进入 DETACHED；
- DETACHED：新 stage / completed / error 不强制改变 `scrollTop`；
- 提供“回到最新消息”，点击后 smooth scroll 并恢复 FOLLOWING；
- 手动或按钮回到底部后恢复 FOLLOWING。

本 Track 只处理前端布局与交互，不改变 API / SSE / Agent / RAG / Auth / Web / MCP 语义。

## 安全渲染

- LLM answer 使用 Vue text binding，不使用 `v-html`；
- Web source title 同样按文本转义；
- 外链只使用服务端 provenance 返回的 http/https URL；
- external link 使用 `target="_blank"` + `rel="noopener noreferrer"`；
- 错误只显示前端安全映射文案与 request_id；
- AbortController 在页面卸载时断开浏览器 fetch，但 HTTP disconnect 不等于后端 generation cancellation。

## 当前测试覆盖

现有测试已覆盖：

- empty state / example prompts；
- composer validation；
- Enter / Shift+Enter；
- mode selection；
- SSE stage / completed / error；
- insufficient evidence；
- duplicate submit；
- local multi-turn rendering；
- history not sent to backend；
- unmount abort；
- auth flows；
- SSE parser；
- XSS-safe text rendering；
- safe Web source links。

UX Track 测试还覆盖 scroll state machine、提交自动到底、SSE follow/detach、error 的相同策略，以及 jump-to-latest。

jsdom 适合验证逻辑状态；真实 CSS viewport / responsive geometry 应在浏览器 smoke 中验证。

## Roadmap

当前顺序：

```text
UX Track   Frontend / Product Experience Stabilization   ✅ COMPLETE
Phase 15   Conversation & Session Memory                 ← NEXT PRODUCT PHASE
Phase 16   Retrieval Intelligence & Knowledge Graph
Phase 17   Agent Analyst
Phase 18   Advanced Agent Autonomy / Bounded ReAct
Phase 19   Owner Agent / Advanced Permissions
```

权威定义见仓库根目录 `docs/roadmap-v2.md`。
