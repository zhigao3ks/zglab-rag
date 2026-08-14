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

目标：提供公网 `/ask` 接口。

实现内容：

- Context Builder；
- 与证据分离的 Persona 规则；
- LLM Provider Adapter；
- 证据不足时的处理；
- 来源引用。

验收标准：

- 回答自然使用第一人称；
- 事实性陈述基于选定 Chunk；
- 未知问题不会触发虚构个人事实。

## Phase 9 — Evaluation Harness

建立带版本的 golden dataset，包含以下查询：

- 身份查询；
- 项目查询；
- 精确技术词查询；
- 跨来源问题；
- 证据不足问题；
- 针对 private 数据的对抗问题。

分别跟踪 Retrieval 和 Generation。

## Phase 10 — 增量同步与生产部署

实现内容：

- Git 来源 revision 检查；
- 变化文档重新索引；
- systemd service；
- Nginx reverse proxy；
- `/var/lib/zglab-rag/` 下的 runtime 数据；
- 健康检查和日志。

不得把生产索引、数据库或模型缓存放在 Git checkout 中。

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
