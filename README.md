# ZGLab RAG

ZGLab RAG 是面向个人公开知识与项目经历的 Personal Knowledge Assistant。

它不是单纯的 Notes 搜索器，而是以黄志高的公开身份、项目、技术知识与实践经验为知识基础，通过 Evidence-Grounded RAG 对外进行介绍、解释与分享。

## 目标

系统最终应能够回答：

- 我是谁、研究和工程方向是什么；
- 我做过哪些项目，以及项目为什么这样设计；
- 某个项目的具体架构、技术选择、问题与经验；
- Notes 中沉淀的技术知识、问题复盘与方法论；
- 公开经历、论文、竞赛与其他可分享信息。

回答采用第一人称表达，但事实必须由可追溯的公开证据支撑。系统不得因为 Persona 而自由补充不存在的经历、指标或项目事实。

## 架构原则

1. **Evidence before Persona**：先检索证据，再以第一人称组织回答。
2. **Public boundary first**：公网助手只能访问明确标记为 `public` 的知识源。
3. **Source registry driven**：知识源通过 `config/sources.yaml` 注册，不把数据源硬编码进业务逻辑。
4. **Retrieval is replaceable**：Embedding、BM25、Vector Store、Reranker 都通过独立边界接入，便于评测和替换。
5. **Do not index everything**：GitHub 仓库只索引精选 README / docs / Markdown，不默认索引源码、构建产物或依赖文件。
6. **Evaluation before optimization**：每次替换 Chunk、Embedding、Reranker 或融合策略都应可比较评测。
7. **Runtime data is not source code**：数据库、模型缓存、索引、日志与密钥不得提交到 Git。

## 当前阶段

当前进入 `Phase 5 - production vector retrieval baseline`：

- 从 `config/sources.yaml` 解析已注册的本地 Markdown 与本地 Git repository；
- Git source 通过显式 `local_path` 和 include allowlist 获取文档；
- exclude 规则优先，文件顺序、document/chunk ID 与 provenance 保持稳定；
- Git Adapter 只读取本地 checkout 和 HEAD revision，不负责 clone、pull 或 fetch；
- 候选 Embedding 模型由 `config/embedding-models.yaml` 注册；
- `evaluation/retrieval.yaml` 保存 50 条可追踪到 section 的检索标注；
- Phase 3 benchmark 仍使用全量 chunk 的内存 cosine，评测输入保持不变；
- `runtime/knowledge.db` 使用普通 SQLite 表持久化 source、document、chunk、embedding
  profile 与 index run；
- `sqlite-vec==0.1.9` 的 vec0 表只保存与 `chunks.id` 对应的 512 维 BGE 向量；
- 增量 planner 按 `chunk_id + embedding_input_hash + embedding_profile_id` 区分
  new/changed/unchanged/deleted，重复 build 不会重新计算未变化向量；
- embedding 在事务外完成，验证成功后才在短事务中原子更新 metadata、vector 与 source snapshot。
- 正式 `VectorRetriever` 只读持久化 index，默认且强制执行 public visibility；
- 支持 source/scope filter、受控 over-fetch、profile validation 与可观测 latency；
- Phase 5 复用 Phase 3 数据集，通过真实 SQLite + sqlite-vec 链路报告 Recall、HitRate、MRR。

后续阶段：

```text
v0  Architecture & source model
 ↓
v1  Markdown ingestion
 ↓
v2  Local source acquisition
 ↓
v3  Embedding benchmark
 ↓
v4  Persistent vector store / index lifecycle
 ↓
v5  Production vector retriever（当前）
 ↓
v6  Hybrid retrieval
 ↓
v7  Lightweight reranker
 ↓
v8  Grounded answer + citations
```

## 目录

```text
zglab-rag/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── config/
│   ├── embedding-models.yaml
│   └── sources.yaml
├── evaluation/
│   └── retrieval.yaml
├── docs/
│   ├── architecture.md
│   └── knowledge-model.md
├── knowledge/
│   └── identity/
│       └── profile.md
├── src/
│   └── zglab_rag/
│       ├── api/
│       ├── domain/
│       ├── embeddings/
│       ├── evaluation/
│       ├── ingestion/
│       ├── indexing/
│       ├── retrieval/
│       ├── generation/
│       ├── storage/
│       └── sources/
└── tests/
```

## 本地开发

建议环境：WSL2 Ubuntu 24.04 + Python 3.12 + `uv`。

```bash
uv sync
uv run uvicorn zglab_rag.api.main:app --reload
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

验证本地 Markdown ingestion：

```bash
uv run python -m zglab_rag.ingestion.cli knowledge/identity/profile.md
```

检查已注册的本地知识源：

```bash
uv run python -m zglab_rag.sources.cli list
uv run python -m zglab_rag.sources.cli inspect notes
```

对一个已注册 source 执行 Markdown ingestion：

```bash
uv run python -m zglab_rag.ingestion.cli --source notes
```

这些命令不会执行 `git pull`、`git fetch` 或其他同步操作；repository 更新由未来独立的
Sync Layer 或部署任务负责。

Chunk 参数可通过 `ZGLAB_RAG_CHUNK_TARGET_SIZE`、
`ZGLAB_RAG_CHUNK_MAX_SIZE` 和 `ZGLAB_RAG_CHUNK_OVERLAP` 配置。

在 `identity-profile` 和 `notes` 的真实 chunk 上运行单个 benchmark：

```bash
uv run python -m zglab_rag.evaluation.embedding_benchmark \
  --source identity-profile \
  --source notes \
  --model bge-small-zh-v1.5 \
  --device cpu \
  --composition contextual
```

`--all` 会运行所有 enabled model 与两种 document composition。运行结果写入已忽略的
`artifacts/benchmarks/`；评测集和模型配置则纳入版本控制。显式请求 `cuda` 但 PyTorch
无法使用 CUDA 时命令会报错，不会静默回退到 CPU。

Benchmark 的 Recall@K 按 relevant section target 计算：同一超长 section 的任一二次切片
命中即视为该 target 命中；多个 relevant target 分别计入 recall。`hard_negative` 在尚未定义
相似度拒绝阈值的 Phase 3 中不进入 Recall/MRR 分母，并会作为 skipped query 记录。
总指标输出 Recall@1/3/5/10/20/30 与 MRR，并按 scored query category 输出对应 breakdown。

## 持久化 Knowledge Index

默认数据库为已忽略的 `runtime/knowledge.db`，可用
`ZGLAB_RAG_DATABASE_PATH` 覆盖。数据库初始化会加载 sqlite-vec、执行
`select vec_version()` 并校验 schema version；扩展加载或版本不匹配会明确失败，不会退化为
Python cosine。

```bash
uv run python -m zglab_rag.indexing.cli status
uv run python -m zglab_rag.indexing.cli plan \
  --source identity-profile --source notes
uv run python -m zglab_rag.indexing.cli build \
  --source identity-profile --source notes
uv run python -m zglab_rag.indexing.cli rebuild \
  --source identity-profile --source notes
uv run python -m zglab_rag.indexing.cli search \
  "Agent 长期记忆和 Context 有什么区别？" --top-k 5
```

`plan` 是只读操作，不会创建数据库。`build` 只计算 new/changed chunks；模型、维度、
composition、normalize 或 query mode 与 active profile 不一致时会拒绝写入。只有显式
`rebuild` 可以替换 active profile，而且 profile 变化时必须覆盖所有已索引 source。
`search` 只是验证 sqlite-vec 持久化 KNN 和 metadata 回表的 public-only smoke test，不是正式
Retriever，也不包含 BM25、融合或 rerank。

## Production Vector Retriever

Phase 5 的正式 CLI：

```bash
uv run python -m zglab_rag.retrieval.cli \
  "Agent 长期记忆和 Context 有什么区别？" --top-k 5 --debug

uv run python -m zglab_rag.retrieval.cli \
  "结构化 LLM 调用" --source notes --scope knowledge

uv run python -m zglab_rag.evaluation.vector_retrieval \
  --source identity-profile --source notes
```

Retriever 默认 `top_k=5`、最大 50，且不提供 private CLI 开关。过滤采用受控 over-fetch：
先从 vec0 取得不含业务 metadata 的 rowid/distance，再由关系查询强制校验 public、source 和
scope；不足 top-k 时扩大候选集，直到补足、耗尽 index 或达到配置上限。Debug 只输出计数、
filter 与 latency，不输出被过滤候选的 metadata。

sqlite-vec 返回 cosine distance。对外结果统一定义 `score = 1 - distance`，因此 score 越高、
distance 越低表示越相关。每次检索会验证 query provider、当前配置与数据库 active embedding
profile 一致；不匹配时明确失败，不会 rebuild 或切换算法。

正式 search 始终执行最大 top-k 限制。离线 evaluator 为了与 Phase 3 的完整排名 MRR 可比，
会在评测进程内读取当前 corpus 的完整排名；这不会放宽 public CLI 的 top-k 配置。

## 安全边界

默认禁止进入公网知识库：

- 公司内部仓库、客户资料、合同原文、内部接口文档；
- Token、密码、SSH Key、Cookie、API Key；
- 私人聊天与未确认事实；
- 未明确允许公开的 private repository 内容；
- 模型缓存、索引数据库、日志与临时文件。

详见 `docs/knowledge-model.md` 与 `config/sources.yaml`。
