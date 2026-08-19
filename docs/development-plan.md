# 开发计划

本路线图用于在 Codex 协助下渐进开发。每个 Phase 都应能够独立测试，不提前实现后续复杂能力。

## Phase 0 — 架构基础

状态：已搭建。

交付内容：

- 来源注册表；
- 公开/私有边界；
- Domain 元数据；
- 可替换的 AI 组件契约；
- FastAPI 健康检查和来源接口；
- Codex 仓库规则。

验收命令：

```bash
uv sync
uv run pytest
uv run ruff check .
uv run uvicorn zglab_rag.api.main:app --reload
```

## Phase 1 — 本地 Markdown Ingestion

状态：已实现。

目标：在不进行 GitHub 同步、不依赖模型的情况下索引 `knowledge/identity/profile.md`。

实现内容：

- frontmatter parser；
- Markdown document loader；
- 标题结构感知 Chunker；
- 确定性的 document/chunk ID；
- SHA-256 内容哈希；
- 单元测试。

验收标准：

- profile 能加载为 `KnowledgeDocument`；
- Chunk 保留标题层级；
- 重复 ingestion 产生稳定 ID；
- visibility 始终为 `public`。

除非下一阶段确实需要，否则暂不添加 Vector DB。

## Phase 2 — Git 知识源适配器

状态：已实现。

目标：从选定的本地 Git 仓库 checkout 中获取已配置的 Markdown 文件。

首批来源：

- `notes`；
- `zglab-website`。

实现内容：

- 相对于项目根目录的本地仓库路径；
- include/exclude 过滤；
- 来源 revision（commit SHA）；
- 来源 URL 追踪。

仓库同步不属于本阶段。适配器不会 clone、pull、fetch 或修改来源仓库。

验收标准：

- 只加载已配置文件；
- 无法自动发现 private 或未注册仓库；
- 重复发现和 ingestion 的结果确定一致。

## Phase 3 — Embedding 评测

状态：已实现。

目标：使用真实 ZGLab 文档选择 Embedding 实现，而不是预先假定模型。

至少比较：

- 本地 CPU 路径；
- 必要时使用 WSL 本地 GPU 路径；
- 可选的 ONNX/量化生产路径。

记录：

- 模型内存；
- 索引维度；
- 查询延迟；
- 文档 Embedding 吞吐；
- 小型 golden dataset 上的检索质量。

仅在完成本阶段后设置默认 `EMBEDDING_MODEL`。

## Phase 4 — Vector Retrieval

状态：已实现持久化 Vector 存储和增量索引生命周期。

目标：形成首个可用的语义搜索。

实现内容：

- 带显式 schema version 的 SQLite 权威元数据存储；
- 为当前 BGE 512 维 profile 固定 sqlite-vec vec0 adapter；
- 确定性的 Embedding profile 和精确 contextual input hash；
- 按来源规划 new/changed/unchanged/deleted；
- 事务安全的增量构建、显式 rebuild 和失败运行审计；
- public-only CLI Vector KNN 冒烟搜索及元数据 join。

初始存储方向：SQLite + 可替换的轻量 Vector 层。

验收标准：

- 对相同内容重复 build 时，Embedding 数量为零；
- fixture 的新增、变化或删除只更新受影响的行；
- Embedding 失败后，之前完成的索引仍可使用；
- 数据库重新打开后，持久化 Vector 仍存在并映射到权威 Chunk；
- public 冒烟搜索不会返回 private visibility。

正式 `/search` API 和生产 Retriever 组合留到后续阶段。Phase 4 不增加 BM25、Hybrid
融合、Reranking、Generation 或来源同步。

## Phase 5 — 生产 Vector Retrieval Baseline

状态：已实现。

目标：将 Phase 4 的冒烟搜索提升为可复用、可评测的只读 `VectorRetriever`。

实现内容：

- 正式的 query/result/filter/diagnostics 契约；
- 当前 Embedding profile 校验；
- public-by-default 的 source/scope 过滤；
- 受控的候选 over-fetch；
- 确定性的 score/distance 语义；
- 持久化 sqlite-vec 的 Recall、HitRate、MRR 和延迟评测；
- 不设置拒答阈值的 hard-negative 分数诊断。

验收标准：

- private 候选不得暴露元数据或进入公开结果；
- 严格过滤后仍能通过有限 over-fetch 填满 top-k；
- 持久化指标与 Phase 3 的 BGE contextual baseline 一致；
- search 保持只读，不构建或同步索引。

## Phase 6 — Hybrid Retrieval

状态：已实现并完成评测；由于首个 RRF baseline 退化，Vector 仍为默认模式。

目标：改善精确技术词和项目名的检索。

实现内容：

- schema v2 SQLite FTS5 trigram 索引及确定性 lexical profile；
- 显式 v1→v2 migration，并在现有原子 apply 事务中管理 FTS 生命周期；
- 带 public/source/scope 关系过滤的 BM25 Lexical Retriever；
- Vector + Lexical 并行检索；
- 候选池各 50、确定且可配置的 RRF；
- Vector/Lexical/Hybrid 指标、分类分析、hard negatives 和延迟。

验收标准：

- 在同一 golden set 上比较 Vector-only、BM25-only 和 Hybrid；
- 保留 benchmark 输出。

有限的列权重比较选择 `title/section/content = 1/1/1`，而不是 `2/2/1`。等权 RRF
（`k=60`）在未变化的数据集上没有超过 Vector baseline，因此 Phase 6 不修改默认模式，
也不引入 Reranker 或拒答阈值。

## Phase 7 — 轻量 Reranker

状态：实现、确定性单元验证和指定模型 CPU benchmark 均已完成。质量得到改善，但考虑当前
2C2G 生产预算，Vector 仍为默认模式。

目标：改善 Top-K 候选的排序。

通过 `Reranker` 契约实现。

已实现边界：

- 独立 YAML 模型注册表和 `RerankerProvider`；
- `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` Torch/CPU 主候选；
- 稳定的 contextual passage composition；
- 仅使用 Vector 的 10/20/30 候选池，以 20 为主要 baseline；
- 确定性重排，同时保留 Vector/Reranker 的 rank 和 score；
- evaluation delta、分类指标、promotion/demotion、hard negatives、延迟和 RSS；
- 生产 CLI 提供 `reranked` 模式，默认仍为 `vector`。

Benchmark 内容：

- 不使用 Reranker；
- 使用候选轻量本地 Reranker；
- 在目标 2C2G Server profile 下观察 CPU 延迟和峰值内存。

如果实测收益不足以抵消资源成本，生产 Reranker 可以保持可选。

指定模型已通过 Hugging Face 镜像下载到 Git ignored 的 runtime 存储中，并完成 SHA-256
校验。在未修改的 47 条查询数据集上，candidate_k=20 将 Recall@1 从 0.5213 提升到
0.6809，将 Recall@5 从 0.7872 提升到 0.8404，将 MRR 从 0.6532 提升到 0.7753，
Recall@20 保持 0.9255。Candidate 20 的 MRR 高于 10 和 30，但 CPU Reranker 中位延迟约
1.74 秒，完整进程峰值约 1.49 GB RSS。因此，Reranking 是经过实测的可选模式，而不是
生产默认模式。详细记录见 `docs/evaluations/phase-7-reranker.md`。

## Phase 8 — 基于证据的回答生成

状态：实现与确定性验证已完成。真实 API smoke test 依赖本地 `.env` 的 LLM 配置；未配置
时不阻塞代码验收，评测会明确报告未运行真实 generation。

目标：提供核心问答闭环（公网 `/ask` API 留待后续阶段接入同一 service）。

实现内容：

- Generation domain model：EvidenceItem、GeneratedAnswer、Citation mapping、GenerationResult；
- ContextBuilder：短 Evidence ID、确定性预算截断、Prompt 注入边界；
- 与证据分离的 Persona 规则；
- OpenAI-compatible GenerationProvider（`.env` 配置，Key 不入日志）；
- claim-level 结构化生成与确定性 CitationValidator；
- 证据不足处理（检索为空 / 模型判定 / 校验无法安全恢复）；
- 失败模型与最多 1 次语义修复重试；
- generation CLI（ask，默认 vector，reranked 显式可选）；
- 独立评测集 `evaluation/generation.yaml` 与确定性评测。

验收标准：

- 回答自然使用第一人称；
- 事实性陈述基于选定 Chunk，引用可映射回 source_path / section_path；
- 未知问题不会触发虚构个人事实；
- private evidence 不进入 context；
- 单元测试不调用真实 LLM。

## Phase 9 — Public Assistant Product Layer

状态：**总体进行中**

- 9A Public API Contract + Security Boundary = 完成
- 9B Status SSE + Request Lifecycle = 完成
- 9C Web Assistant UI = 未开始
- 9D Integration Acceptance = 未开始

核心目标：把 Phase 8 验证完成的 `GroundedAnswerService` 包装成公网访客可用的产品层。

Phase 8 回答「系统怎样可靠回答问题」；Phase 9 回答「访客怎样可靠地使用系统」。

Phase 9 不再优化 Chunking、Embedding、Vector Index、Retrieval algorithm、Hybrid、
Reranker、Grounding 或 Citation rules——除非 API 集成暴露明确 bug，否则这些能力在
Phase 9 视为冻结。

实现内容（至少六类能力）：

1. **Public API Contract**：`POST /api/v1/ask`，公网请求尽量窄（只接受 `question`），
   `retrieval_mode` / `visibility` / `source_ids` / `top_k` / `provider` / `model` /
   `debug` / `private mode` 全部由服务端控制。响应包含 `request_id` / `status` /
   `answer` / `sources`，默认不暴露 chunk id、embedding score、reranker score、
   provider details、token usage、repair count 或 internal diagnostics。
2. **Streaming / Long Request UX**：v1 不直接流式发送未经 Citation Validation 的
   LLM raw tokens；优先采用 status streaming / SSE（`retrieving → generating →
   validating → completed`），最终 answer 必须在 structured generation + citation
   validation 完成后才发送。真正的 token streaming 留到 Post-v1 Optimization。
3. **Public Security Boundary**：question length limit、request body limit、
   request timeout、rate limit、concurrency limit、public-only retrieval、
   safe error mapping、CORS allowlist、secret isolation。禁止 private retrieval、
   public debug mode、stack trace 泄露、provider secret 泄露。
4. **API Error Model**：统一公网错误语义（`INVALID_REQUEST` / `RATE_LIMITED` /
   `SERVICE_BUSY` / `GENERATION_TIMEOUT` / `PROVIDER_UNAVAILABLE` / `INTERNAL_ERROR`）；
   `insufficient_evidence` 不是系统异常，而是正常业务结果（`status=insufficient_evidence`）。
5. **Web Assistant Experience**：简单的 Personal Knowledge Assistant UI，第一版重点：
   问题输入、回答展示、Sources 展示、loading/status、copy answer、error state、
   mobile responsive。后端仍保持每次 question → 独立 retrieval → 独立 grounded
   generation；Phase 9 不实现 Conversation Memory。
6. **Acceptance**：覆盖「你是谁？」→ grounded answer + sources；「你做过哪些 Agent
   项目？」→ project-grounded answer；「Memory 和 Context 有什么区别？」→ technical
   answer + citation；知识库不存在的问题 → insufficient evidence；超长输入 → rejected；
   provider timeout → safe public error；并发超过限制 → busy / rate limited；
   prompt injection → system rules 不受影响；public request → 永远无法访问 private
   evidence。

## Phase 10 — Production Sync & Deployment

状态：待实现。

核心目标：让 Phase 9 的产品能够持续更新知识 + 稳定运行在生产服务器。Phase 10 不增加
新的 RAG 算法能力。

实现内容：

1. **Source Sync Layer**：Phase 2 的 `LocalGitSource` 继续保持 read-only；新增
   Sync Layer 负责 remote revision check / `git fetch` / fast-forward / revision
   before-after。原则：Sync Layer 负责更新 checkout，Source Adapter 只负责读取
   checkout。
2. **Incremental Reindex Pipeline**：最终生产链路：`revision unchanged → skip`；
   `revision changed → ingestion → chunk diff → new/changed/unchanged/deleted →
   only embed new+changed → vector update → FTS update → atomic apply`。复用
   Phase 4 已完成的 Index Planner 与 Incremental Index Lifecycle。重要原则：
   Source sync failure ≠ Serving failure——同步失败时继续使用旧 `knowledge.db`
   提供问答。
3. **Production Runtime Layout**：
   ```text
   /opt/zglab-rag/           application code
   /opt/zglab-sources/       notes/ zglab-website/ resume-tailor-agent/ ...
   /var/lib/zglab-rag/       knowledge.db  models/  cache/
   /var/log/zglab-rag/       application logs  sync logs
   /etc/zglab-rag/           production env
   ```
   禁止把 `knowledge.db`、models、source checkouts、runtime cache 长期放在
   Git checkout 中。
4. **Production Service**：保持轻量 `Internet → Nginx → FastAPI/Uvicorn →
   SQLite + sqlite-vec → local BGE → external LLM API`。服务：
   `zglab-rag.service`；同步：`zglab-rag-sync.service` + `zglab-rag-sync.timer`。
   当前 2C2G 环境不引入 Kubernetes / Redis / Celery / Kafka / Milvus / Qdrant /
   Elasticsearch——除非未来有明确需求。
5. **Health / Readiness**：`GET /health`（进程正常）与 `GET /ready`（核心依赖
   可服务：database、sqlite-vec、embedding profile、generation config）。
6. **Lightweight Observability**：至少记录 `request_id` / `status` /
   retrieval latency / generation latency / total latency / provider status /
   token usage（如果可得）/ repair attempts / insufficient count / error category。
   禁止记录 API Key、完整 private evidence、secret、未经必要处理的敏感 Prompt。

## Evaluation 的新定位

取消旧路线中「Phase 9 = Evaluation Harness」作为独立 Phase 的定义。Evaluation
不是被删除，而是被重新定义为**贯穿整个项目的持续性基础设施**：

- Phase 3 Embedding Evaluation
- Phase 5 Vector Retrieval Evaluation
- Phase 6 Hybrid Evaluation
- Phase 7 Reranker Evaluation
- Phase 8 Generation Evaluation
- Phase 9 / Phase 10 继续作为 regression / acceptance 基础设施

后续新增功能时允许增加 regression cases，但不再单独建设一个「Evaluation Phase」。
已有的 `evaluation/retrieval.yaml`、`evaluation/generation.yaml`、
`artifacts/benchmarks/` 与 `artifacts/evaluation/` 继续作为项目一等模块维护。

## Post-v1 Optimization

以下能力作为持续优化方向，**不作为 Phase 11 强行编号**，也不构成 Phase 9 / Phase 10
的验收前置条件：

- Reranker ONNX / INT8 量化与生产 enable evaluation
- Answer latency optimization
- max output tokens 调优
- Real token streaming（在 Citation Validation 之后）
- Caching（embedding / retrieval / answer）
- Richer monitoring / metrics
- Evaluation expansion（更多 category、hard negative、LLM Judge 探索）
- Answerability / rejection threshold 研究
- Conversation context / memory
- Advanced Hybrid tuning（RRF 参数、列权重、score normalization）

这些方向在 Phase 9 / Phase 10 验收后按需启动。

## Codex 任务规则

要求 Codex 实现 Phase 时，每次任务只包含一个 Phase 或一个垂直切片。

一个良好的任务应包含：

```text
目标
允许修改的文件
必须保持稳定的契约
必需测试
明确不做的内容
验收命令
```

避免使用“完成整个 RAG 系统”之类的提示。本项目有意将每个组件设计为可独立实现、测量和审核。
