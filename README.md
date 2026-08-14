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

当前处于 `v1 - local Markdown ingestion`：

- 从 `config/sources.yaml` 解析已注册的本地 Markdown；
- 使用 YAML Frontmatter 构建可追溯的 `KnowledgeDocument`；
- 按 Markdown 标题层级生成稳定、带 visibility 的 `KnowledgeChunk`；
- 超长章节按配置进行二次切分；
- 暂不实现 Embedding、索引和检索。

后续阶段：

```text
v0  Architecture & source model
 ↓
v1  Markdown ingestion
 ↓
v2  BM25 + vector hybrid retrieval
 ↓
v3  Lightweight reranker
 ↓
v4  grounded answer + citations
 ↓
v5  evaluation harness
 ↓
v6  source sync / incremental indexing
 ↓
v7  intent routing / agentic retrieval
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
│   └── sources.yaml
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

Chunk 参数可通过 `ZGLAB_RAG_CHUNK_TARGET_SIZE`、
`ZGLAB_RAG_CHUNK_MAX_SIZE` 和 `ZGLAB_RAG_CHUNK_OVERLAP` 配置。

## 安全边界

默认禁止进入公网知识库：

- 公司内部仓库、客户资料、合同原文、内部接口文档；
- Token、密码、SSH Key、Cookie、API Key；
- 私人聊天与未确认事实；
- 未明确允许公开的 private repository 内容；
- 模型缓存、索引数据库、日志与临时文件。

详见 `docs/knowledge-model.md` 与 `config/sources.yaml`。
