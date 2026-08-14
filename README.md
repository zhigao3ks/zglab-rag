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

当前处于 `Phase 3 - embedding evaluation & benchmark`：

- 从 `config/sources.yaml` 解析已注册的本地 Markdown 与本地 Git repository；
- Git source 通过显式 `local_path` 和 include allowlist 获取文档；
- exclude 规则优先，文件顺序、document/chunk ID 与 provenance 保持稳定；
- Git Adapter 只读取本地 checkout 和 HEAD revision，不负责 clone、pull 或 fetch；
- 候选 Embedding 模型由 `config/embedding-models.yaml` 注册；
- `evaluation/retrieval.yaml` 保存 50 条可追踪到 section 的检索标注；
- benchmark 只使用全量 chunk 的内存 cosine，不实现持久化索引或生产检索。

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
v4  Vector retrieval
 ↓
v5  Hybrid retrieval
 ↓
v6  Lightweight reranker
 ↓
v7  Grounded answer + citations
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
│       ├── retrieval/
│       ├── generation/
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

## 安全边界

默认禁止进入公网知识库：

- 公司内部仓库、客户资料、合同原文、内部接口文档；
- Token、密码、SSH Key、Cookie、API Key；
- 私人聊天与未确认事实；
- 未明确允许公开的 private repository 内容；
- 模型缓存、索引数据库、日志与临时文件。

详见 `docs/knowledge-model.md` 与 `config/sources.yaml`。
