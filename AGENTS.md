# AGENTS.md

本文件约束 Codex / 其他编码 Agent 在 `zglab-rag` 中的工作方式。

## 1. Project Goal

本项目构建 ZGLab Personal Knowledge Assistant。

系统面向公网访客，以黄志高的第一人称进行介绍、项目解释和知识分享，但任何事实性回答必须来自可追溯、允许公开的知识源。

核心原则：

```text
Evidence
→ Retrieval
→ Context
→ Grounded Generation
→ Citation
```

Persona 只影响表达方式，不允许覆盖事实边界。

## 2. Architectural Boundaries

保持以下层次独立：

- `sources/`：知识源获取与同步；
- `ingestion/`：解析、规范化、切片、写入索引；
- `retrieval/`：BM25、Vector、融合、Rerank；
- `generation/`：Context 构建、Prompt、LLM、引用；
- `domain/`：与框架无关的数据模型；
- `api/`：HTTP API，只负责协议层。

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

公网 API 的检索入口必须默认强制：

```text
visibility == public
```

即使数据库中未来存在 private 文档，也不得因为召回分数较高而进入公网 Context。

任何涉及 private 模式的能力都必须单独设计鉴权，不得复用公网默认行为隐式开放。

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

第一阶段不要假设永久使用某个具体模型或框架。

优先保留以下可替换能力：

```text
EmbeddingProvider
VectorIndex
LexicalRetriever
Reranker
Generator
```

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

## 8. Runtime Data

以下内容不得提交到 Git：

- `.env`
- SQLite/Vector DB 数据库
- 模型权重与 Hugging Face cache
- 索引文件
- 运行日志
- 临时抓取文件
- API Key / Token / Cookie / SSH Key

运行数据统一放在 Git ignored 的 `runtime/` 或生产环境 `/var/lib/zglab-rag/`。

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
- 不执行破坏性 Git 操作。

读取外部公开知识源与写入外部仓库是两种不同权限。

## 11. Testing

新增功能至少覆盖：

- 正常路径；
- visibility 边界；
- 无证据/空召回；
- 配置错误；
- 重复 ingestion 或幂等行为（相关模块涉及时）。

任何公网检索测试都应包含“private 文档不会被返回”的断言。

## 12. Definition of Done

一个功能完成至少满足：

1. 边界清楚；
2. 配置可追踪；
3. 有测试；
4. 不泄露 private source；
5. 不引入未经证明的事实；
6. `ruff` 与 `pytest` 通过；
7. README / docs 在架构变化时同步更新。

## 13. Documentation Language

README、`docs/` 和新增的项目说明文档默认使用中文书写。

模型名、类名、接口名、配置字段、指标、命令及行业通用技术术语可以保留英文；除非存在明确的
对外英文文档需求，不要新增整篇英文说明文档。
