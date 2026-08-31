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

Phase 14 已完成 Agent 产品接入并生产封板；当前前端最高优先级是非编号 UX Track，不是 Phase 15 Session Memory。

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
│   │   └── types.ts
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

## 当前布局现状

现有 `AssistantView.vue` 是纵向页面：

```text
account bar
password form（optional）
AssistantHeader
app-main
  ├── ConversationView
  └── QuestionComposer
footer
```

当前主要 UX 问题：

- document 承担长会话滚动；
- message list 不是独立 scroll container；
- Composer 会随消息向页面底部移动；
- completed 后不会自动定位最新消息；
- SSE 没有 near-bottom smart follow；
- 用户主动上翻时没有 detached 状态；
- 没有“回到最新消息”；
- Header / Navigation / Composer 的 narrow viewport 适配不足；
- 当前单 column shell 不利于未来 Session Sidebar。

## UX Track 目标

目标结构：

```text
Assistant Layout
├── future Session Sidebar slot
└── Workspace
    ├── Header / Navigation
    └── Chat Area
        ├── independent Message Scroll Area
        └── Composer Dock
```

建议滚动逻辑采用：

```text
FOLLOWING
↕
DETACHED
```

- send：主动恢复 FOLLOWING 并定位最新；
- near bottom：SSE / completed 自动跟随；
- user scrolls up：进入 DETACHED；
- DETACHED：新 stage / completed 不强制改变 scrollTop；
- 提供“回到最新消息”；
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

UX Track 应新增 scroll state machine 与 jump-to-latest 行为测试。

jsdom 适合验证逻辑状态；真实 CSS viewport / responsive geometry 应在浏览器 smoke 中验证。

## Roadmap

当前顺序：

```text
UX Track   Frontend / Product Experience Stabilization   ← NOW
Phase 15   Conversation & Session Memory
Phase 16   Retrieval Intelligence & Knowledge Graph
Phase 17   Agent Analyst
Phase 18   Advanced Agent Autonomy / Bounded ReAct
Phase 19   Owner Agent / Advanced Permissions
```

权威定义见仓库根目录 `docs/roadmap-v2.md`。
