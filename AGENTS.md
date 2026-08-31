# AGENTS.md

本文件约束 Codex / 其他编码 Agent 在 `zglab-rag` 中的工作方式。

## 1. Project Goal

本项目已经完成 Personal Knowledge Assistant Foundation，并已演进为生产可用的 **ZGLab Personal AI Agent**。

当前正式能力：

```text
Personal Knowledge Skill
+ Web Research Skill
+ MCP Tool Runtime
+ Bounded Agent Orchestration
```

核心事实性原则始终是：

```text
Evidence
→ Retrieval / Research
→ Context
→ Grounded Generation
→ Citation
```

Persona 只影响表达方式，不允许覆盖事实边界。

## 2. Roadmap Authority

自 **2026-08-25** 起，`docs/roadmap-v2.md` 是 Phase 11+ 的权威路线图。

当前状态：

```text
Phase 0–10  Personal Knowledge Assistant Foundation        ✅ SEALED
Phase 11    Authentication & Access Control                ✅ SEALED
Phase 12    Capability Foundation & Web Research           ✅ SEALED
Phase 13    MCP Tool Runtime                               ✅ SEALED
Phase 14    Agent Orchestrator                              ✅ COMPLETE / PRODUCTION ACCEPTED / SEALED

UX Track     Frontend / Product Experience Stabilization   ← IMMEDIATE

Phase 15    Conversation & Session Memory                   ← NEXT PRODUCT PHASE
Phase 16    Retrieval Intelligence & Knowledge Graph
Phase 17    Agent Analyst
Phase 18    Advanced Agent Autonomy / Bounded ReAct
Phase 19    Owner Agent / Advanced Permissions
```

如果 README、旧 architecture、development-plan、历史 acceptance、issue、comment 或旧 prompt 与上述 Phase 11+ 顺序冲突，以 `docs/roadmap-v2.md` 为准。

历史 evaluation / acceptance 文档记录当时事实，不因新 Roadmap 而改写。

**不得擅自扩展已 SEALED 的 Phase 11–14。**
当前未获得明确授权时，不得开始 Phase 15+。

## 3. 当前立即任务边界

Phase 14 封板后的最高优先级是 UX Track：

- viewport-bounded frontend shell；
- independent message scroll；
- composer dock / sticky experience；
- smart auto-follow；
- jump-to-latest；
- responsive header / navigation；
- 为未来 Session Sidebar 预留结构。

UX Track 不得顺手实现：

- conversation persistence；
- conversation_id / message database；
- multi-turn context assembly；
- session evidence reuse；
- Agent replanning / retry / ReAct；
- owner-only / write capability。

## 4. Architectural Boundaries

保持以下层次独立：

- `sources/`：知识源获取与同步；
- `ingestion/`：解析、规范化、切片、写入索引；
- `retrieval/`：Vector、BM25、Hybrid、Rerank；
- `generation/`：Context、Prompt、LLM、Citation；
- `capabilities/`：稳定 Skill boundary；
- `research/`：受控 Web Research；
- `mcp/`：MCP Host runtime；
- `agent/`：Planner / Executor / Observation / Synthesis；
- `auth/`：身份、Session、安全与 quota lifecycle；
- `domain/`：framework-free domain model；
- `api/`：HTTP / SSE 协议层。

禁止把认证、GitHub 拉取、Markdown 解析、Embedding、检索、LLM、工具调用和 Agent orchestration 堆在一个 service 中。

## 5. Frozen Agent Invariants

Phase 14 第一版必须继续保持：

```text
max steps = 4
Personal <= 1
Web <= 1
MCP <= 3
deterministic planner
sequential executor
no automatic retry
no replanning
no infinite ReAct
```

同时必须保持：

- **Evidence before Persona**；
- **ToolResult != Evidence**；
- Web Evidence 是 untrusted data；
- Web content 不能修改冻结计划；
- MCP Host allowlist 才是工具授权边界；
- Planner proposes, Executor enforces；
- Auth / quota / concurrency / kill switch 位于 Agent Runtime 外部；
- SSE 不暴露 plan、observation、tool raw data、网页正文或推理。

## 6. Knowledge Source Rules

所有正式知识源必须注册在 `config/sources.yaml`。

不得：

- 默认扫描用户所有 GitHub 仓库；
- 默认索引 private repository；
- 默认索引源码、lockfile、构建产物或依赖目录；
- 将公司内部仓库、客户资料或合同内容加入公网索引；
- 绕过 `visibility` 过滤。

新增知识源必须显式定义 scope、visibility、include/exclude 与 provenance。

## 7. Public / Private Boundary

当前生产知识检索继续强制：

```text
visibility == public
```

登录不等于 private knowledge 自动开放。
Private / owner-only knowledge 只允许在未来 Phase 19 单独设计授权、审计与 step-up policy。

## 8. Factuality

禁止模型自由补充：

- 未确认的项目指标；
- 未确认的工作/实习经历；
- 未确认的论文状态；
- 未确认的技术实现；
- 未确认的项目状态与日期。

证据不足时返回不足以确认，不生成看似合理的事实。

## 9. Replaceable AI Components

Embedding、Vector Store、BM25、Reranker、LLM、SearchProvider、MCP Client 与 Agent capability 必须通过清晰接口隔离。

不要让 LangChain / LlamaIndex 等框架类型泄漏到核心 Domain Model。

## 10. Retrieval / Agent Development Rule

修改 Chunk、Embedding、BM25、Hybrid、Reranker、Top-K、hierarchical retrieval、graph retrieval 或 Agent strategy 时，必须保留可由 Evaluation Harness 比较的入口。

禁止只凭主观体验宣布“效果变好了”。Evaluation 是跨阶段基础设施。

## 11. Runtime Data

以下内容不得提交 Git：

- `.env`；
- SQLite / Vector DB；
- auth/session database；
- 模型权重与 Hugging Face cache；
- 索引文件；
- 日志；
- 临时抓取文件；
- API Key / Token / Cookie / Session Secret / Activation Token / SSH Key。

生产 runtime 使用既有 `/opt/zglab-rag/runtime/` 等目录。

## 12. Local Development

默认环境：WSL2 Ubuntu 24.04 + Python 3.12 + `uv`。

```bash
uv sync
uv run pytest
uv run ruff check .
uv run uvicorn zglab_rag.api.main:app --reload
```

Web：

```bash
cd web
npm run test:run
npm run build
```

不要向系统 Python 直接安装项目依赖。

## 13. Git / Repository Writes

修改仓库前先检查当前 main / branch 状态，避免覆盖并发修改。

除非用户在当前任务明确授权，否则：

- 不修改其他 GitHub 仓库；
- 不向 Notes / Website / Project repositories 写入；
- 不创建或修改 release / tag；
- 不执行破坏性 Git 操作；
- 不主动部署生产服务器。

读取外部信息与写入外部仓库是两种不同权限。

## 14. Testing

新增功能至少覆盖正常路径与其关键边界。

相关模块涉及时还应覆盖：

- visibility / authorization；
- 空召回 / insufficient evidence；
- 配置错误；
- quota / concurrency / kill switch；
- 幂等与 revoke；
- Agent budget / unauthorized tool execution；
- Web untrusted / provenance；
- frontend UX 状态机与回归。

认证和授权必须由服务端测试证明，不能只依赖前端 route guard 或隐藏按钮。

## 15. Definition of Done

一个功能完成至少满足：

1. 边界清楚；
2. 配置可追踪；
3. 有测试；
4. 不泄露 private source / credential；
5. 不引入未经证明的事实；
6. 对应 lint / test / build 通过；
7. README / docs 在架构变化时同步；
8. 不擅自扩展 SEALED Phase；
9. 不提前实现后续 Phase。

## 16. Documentation Language

README、`docs/` 和新增项目说明默认使用中文。
模型名、类名、接口名、配置字段、指标、命令和行业通用技术术语可以保留英文。
