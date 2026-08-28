# AGENTS.md

本文件约束 Codex / 其他编码 Agent 在 `zglab-rag` 中的工作方式。

## 1. Project Goal

本项目已经完成 Personal Knowledge Assistant 的生产基础，并正在向 **ZGLab Personal AI Agent** 演进。

当前已完成的核心基础仍然是：系统以黄志高的公开身份、项目与技术知识为知识基座，通过
Evidence-Grounded RAG 对外进行介绍、项目解释和知识分享；任何事实性回答必须来自可追溯、
允许公开的知识源。

长期目标是在现有 RAG 基础上逐步增加：

```text
Personal Knowledge Skill
+ Web Research Skill
+ MCP Tool Runtime
+ Agent Orchestration
```

但不得提前实现未到达的 Phase。

核心事实性原则继续保持：

```text
Evidence
→ Retrieval / Research
→ Context
→ Grounded Generation
→ Citation
```

Persona 只影响表达方式，不允许覆盖事实边界。

## 1.1 Roadmap Authority（重要）

自 **2026-08-25** 起，`docs/roadmap-v2.md` 是 **Phase 11 及以后阶段的权威路线图**。

Phase 0–10 的历史编号、实现内容和验收记录保持不变。

仓库历史上曾冻结：

```text
Phase 11 = External Research & Session Evidence
```

该编号已被新的 Roadmap supersede。Web Research 技术设计本身仍然有效，但现顺延到 Phase 12；
Session Context 统一移动到 Phase 15。

当前权威路线：

```text
Phase 0–10  Personal Knowledge Assistant Foundation     ✅
Phase 11    Authentication & Access Control             ✅ SEALED
Phase 12    Agent Capability Foundation & Web Research  ✅ SEALED
Phase 13    MCP Tool Runtime                            ✅ SEALED
Phase 14A   Agent Contracts & Observation Model         ✅
Phase 14B   Router / Bounded Planner                    ✅
Phase 14C   Executor & Final Synthesis                  ✅
Phase 14D   Product / Evaluation / Production           ⏳
Phase 15    Session Context
Phase 16    Owner Agent / Advanced Permissions
```

如果以下内容出现冲突：

- `README.md`
- `docs/development-plan.md`
- `docs/architecture.md`
- `docs/public-api.md`
- `docs/web-research-skill.md`
- 历史 `docs/evaluations/*`
- issue / comment / old prompt

对于 **Phase 11+ 的编号与执行顺序**，必须以 `docs/roadmap-v2.md` 为准。

历史 acceptance 文档中出现的 “future Phase 11” 只是当时的规划，不得据此开始开发 Web Research。

当前在未获得新的明确授权时，**不得实现 Phase 13D（Security / Evaluation / Production）、Agent Planner、Session Memory 或 Owner Agent**。

## 2. Architectural Boundaries

保持以下层次独立：

- `sources/`：知识源获取与同步；
- `ingestion/`：解析、规范化、切片、写入索引；
- `retrieval/`：BM25、Vector、融合、Rerank；
- `generation/`：Context 构建、Prompt、LLM、引用；
- `domain/`：与框架无关的数据模型；
- `api/`：HTTP API，只负责协议层。

Phase 11 起新增的 Auth / Agent 能力也必须保持独立边界，不得把身份认证、业务问答、检索和
LLM 调用堆在同一个 service 中。

禁止把 GitHub 拉取、Markdown 解析、Embedding、检索和 LLM 调用全部写在一个 service 中。

## 3. Knowledge Source Rules

所有正式知识源必须注册在 `config/sources.yaml`。

不得：

- 默认扫描用户所有 GitHub 仓库；
- 默认索引 private repository；
- 默认索引源码、lockfile、构建产物或依赖目录；
- 将公司内部仓库、客户资料或合同内容加入公网索引；
- 绕过 `visibility` 过滤。

新增知识源时应显式定义：

- `id`
- `kind`
- `scope`
- `visibility`
- `priority`
- `include`
- `exclude`

## 4. Public / Private Boundary

现有知识检索入口必须继续强制：

```text
visibility == public
```

即使数据库中未来存在 private 文档，也不得因为召回分数较高而进入公开 Context。

Phase 11 引入登录不等于自动开放 private knowledge。任何 private mode 都必须在未来 Owner Agent /
Advanced Permissions 阶段单独设计，不得复用公网默认行为隐式开放。

## 5. Factuality

禁止模型自由补充：

- 未确认的项目指标；
- 未确认的工作/实习经历；
- 未确认的论文状态；
- 未确认的技术实现；
- 未确认的项目状态与日期。

证据不足时，系统应返回“不足以确认”，而不是生成一个看起来合理的答案。

## 6. Replaceable AI Components

Embedding、Vector Store、BM25、Reranker、LLM 必须通过清晰接口隔离。

优先保留以下可替换能力：

```text
EmbeddingProvider
VectorIndex
LexicalRetriever
Reranker
Generator
```

未来 SearchProvider、MCP Client 与 Agent capability 也应遵循相同的可替换边界。

不要让 LangChain/LlamaIndex 等框架类型泄漏到核心 Domain Model。

## 7. Retrieval Development Rule

每次新增或修改以下内容：

- Chunk 策略；
- Embedding 模型；
- BM25 参数；
- Hybrid 融合；
- Reranker；
- Top-K；

都应保留可以被 Evaluation Harness 比较的入口。

禁止只凭主观体验宣布检索“变好了”。

Evaluation 是跨 Phase 基础设施，不重新占用独立 Phase 编号。

## 8. Runtime Data

以下内容不得提交到 Git：

- `.env`
- SQLite/Vector DB 数据库
- Auth database / Session database
- 模型权重与 Hugging Face cache
- 索引文件
- 运行日志
- 临时抓取文件
- API Key / Token / Cookie / Session Secret / Activation Token / SSH Key

运行数据统一放在 Git ignored 的 `runtime/` 或生产环境 `/var/lib/zglab-rag/`、
`/opt/zglab-rag/runtime/` 等既有生产 runtime 目录。

## 9. Local Development

默认开发环境：

- WSL2 Ubuntu 24.04
- Python 3.12
- `uv`

优先命令：

```bash
uv sync
uv run pytest
uv run ruff check .
uv run uvicorn zglab_rag.api.main:app --reload
```

不要向系统 Python 直接安装项目依赖。

## 10. Git / Repository Writes

修改本仓库前先检查当前状态，避免覆盖用户未提交工作。

除非用户在当前任务中明确授权，否则：

- 不修改其他 GitHub 仓库；
- 不向 Notes / Website / Project repositories 写入内容；
- 不创建或修改远端 release / tag；
- 不执行破坏性 Git 操作；
- 不主动部署生产服务器。

读取外部公开知识源与写入外部仓库是两种不同权限。

## 11. Testing

新增功能至少覆盖：

- 正常路径；
- visibility / authorization 边界；
- 无证据/空召回（相关模块涉及时）；
- 配置错误；
- 重复 ingestion、token single-use、session revoke 或其他幂等行为（相关模块涉及时）。

任何公开检索测试都应包含“private 文档不会被返回”的断言。

任何认证/授权能力都必须由服务端测试证明，不能只依赖前端 route guard 或隐藏按钮。

## 12. Definition of Done

一个功能完成至少满足：

1. 边界清楚；
2. 配置可追踪；
3. 有测试；
4. 不泄露 private source / credential；
5. 不引入未经证明的事实；
6. `ruff` 与 `pytest` 通过；
7. README / docs 在架构变化时同步更新；
8. 没有提前实现后续 Phase。

## 13. Documentation Language

README、`docs/` 和新增的项目说明文档默认使用中文书写。

模型名、类名、接口名、配置字段、指标、命令及行业通用技术术语可以保留英文；除非存在明确的
对外英文文档需求，不要新增整篇英文说明文档。
