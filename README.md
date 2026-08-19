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

当前进入 `Phase 8 - grounded generation and citation`：

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
- schema v2 增加 SQLite FTS5 trigram index，`rowid` 与 `chunks.id` 一致；
- `LexicalRetriever` 以关系表强制 public/source/scope filter；
- `HybridRetriever` 以配置化 RRF 融合 vector/lexical rank，不混合不同 score 尺度；
- 当前同一评测集上的 Hybrid 低于 Vector，因此默认仍为 `vector`。
- Phase 7 新增独立 `RerankerProvider` 和 Vector Top-N → CrossEncoder 重排管线；
- 候选模型注册在 `config/reranker-models.yaml`，主候选是
  `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`，CPU/Torch、candidate_k=20；
- 结果保留 original vector rank/score 与 reranker rank/score，重排不能引入 Top-N 外 chunk；
- 指定模型已通过 Hugging Face 镜像下载并完成真实 CPU benchmark；candidate_k=20 时
  Recall@1 从 0.5213 提升到 0.6809，MRR 从 0.6532 提升到 0.7753；
- 质量收益明确，但重排 CPU 中位延迟约 1.74 秒、进程峰值 RSS 约 1.49 GB，因此生产默认
  仍为 `vector`，`reranked` 作为显式可选模式保留。
- Phase 8 建立 Question → Retrieval → Evidence Context → External LLM → Grounded Answer
  → Citation Validation 的确定性问答闭环；固定 workflow，不是 Agent loop；
- 每次请求为 Evidence 分配短 ID（E1、E2…），LLM 只引用短 ID，系统校验后映射回
  chunk_id / source_path / section_path；
- 结构化生成采用 claim-level citation JSON；Citation 合法性、归属、覆盖率与
  insufficient-evidence 规则全部由代码确定性校验，不依赖 Prompt 约束；
- Context Budget 确定性截断：默认 top_k=5、最多 5 条 Evidence、6000 字符，只保留完整 chunk；
- OpenAI-compatible `GenerationProvider` 通过 `.env` 配置 base_url / api_key / model，
  Key 永不进入日志或诊断；语义修复重试最多 1 次；
- 检索为空、模型判定不足或校验无法安全恢复时返回“当前公开知识库中没有足够信息回答这个问题”；
- 独立评测集 `evaluation/generation.yaml`（22 条，含 3 条 hard negative）报告确定性
  generation 指标；不修改 `evaluation/retrieval.yaml`。
- 生产默认 Retriever 仍为 `vector`；`reranked` 仅在显式选择时加载。

Phase 9A 实现公网 API 契约与安全边界：

- `POST /api/v1/ask` 窄公网接口，只接受 `question`，服务端强制 `visibility=public`；
- Public response 只包含 `request_id` / `status` / `answer` / `sources`，不泄露 chunk_id、
  score、provider、diagnostics 等内部信息；
- 统一错误 envelope（`INVALID_REQUEST` / `RATE_LIMITED` / `SERVICE_BUSY` /
  `GENERATION_TIMEOUT` / `PROVIDER_UNAVAILABLE` / `INTERNAL_ERROR`），不暴露 traceback；
- 进程内 Concurrency Guard（默认 1 并发）和 Rate Limiter（默认 10 req/min）防止过载；
- Request body limit（16 KiB）、question length limit（1-1000 字符）、CORS allowlist；
- 应用启动加载 BGE 模型，请求级别 SQLite connection，避免每请求重新加载模型；
- CLI 与 HTTP API 共用 `application/runtime.py` 中的 factory，避免配置漂移；
- 完整设计见 [`docs/public-api.md`](docs/public-api.md)。

后续阶段：

```text
Phase 0  Architecture                        ✅
Phase 1  Markdown Ingestion                  ✅
Phase 2  Knowledge Source Acquisition        ✅
Phase 3  Embedding Evaluation                ✅
Phase 4  Persistent Index Lifecycle          ✅
Phase 5  Production Vector Retrieval         ✅
Phase 6  Lexical / Hybrid Evaluation         ✅
Phase 7  Reranker Evaluation                 ✅
Phase 8  Grounded Generation                 ✅
Phase 9  Public Assistant Product Layer       （当前）
Phase 10 Production Sync & Deployment
```

Evaluation 不是独立 Phase，而是贯穿项目的基础设施：
Phase 3 Embedding Evaluation → Phase 5 Vector Retrieval Evaluation →
Phase 6 Hybrid Evaluation → Phase 7 Reranker Evaluation →
Phase 8 Generation Evaluation → Phase 9/10 继续作为 regression / acceptance 基础设施。

Post-v1 Optimization（性能、Reranker 优化、streaming、cache、monitoring、
evaluation expansion、answerability 等）不作为独立 Phase 编号。

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
│   ├── reranker-models.yaml
│   └── sources.yaml
├── evaluation/
│   ├── retrieval.yaml
│   └── generation.yaml
├── docs/
│   ├── architecture.md
│   ├── knowledge-model.md
│   ├── generation-grounding.md
│   └── public-api.md
├── knowledge/
│   └── identity/
│       └── profile.md
├── src/
│   └── zglab_rag/
│       ├── api/
│       ├── application/
│       ├── domain/
│       ├── embeddings/
│       ├── evaluation/
│       ├── ingestion/
│       ├── indexing/
│       ├── retrieval/
│       ├── generation/
│       ├── reranking/
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

Phase 6 schema v2 migration 是显式操作。它保留 vec0，只从 canonical `chunks` 表回填 FTS：

```bash
uv run python -m zglab_rag.storage.migrations runtime/knowledge.db
```

迁移生产数据前应先在 `runtime/` 内建立可恢复备份；迁移不会运行 ingestion 或 embedding。

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

## Production Retrieval

Phase 5 Vector baseline 与 Phase 6 Lexical/Hybrid 共用正式 CLI，默认 mode 仍为 vector：

```bash
uv run python -m zglab_rag.retrieval.cli search \
  "Agent 长期记忆和 Context 有什么区别？" --mode vector --top-k 5 --debug

uv run python -m zglab_rag.retrieval.cli search \
  "generation fencing" --mode lexical --source notes --scope knowledge

uv run python -m zglab_rag.retrieval.cli search \
  "结构化 LLM 调用" --mode hybrid --debug

uv run python -m zglab_rag.retrieval.cli search \
  "Agent 长期记忆和 Context 有什么区别？" \
  --mode reranked --candidate-k 20 --top-k 5 --debug \
  --reranker-model-path runtime/models/mmarco-mMiniLMv2-L12-H384-v1

uv run python -m zglab_rag.evaluation.retrieval_compare
uv run python -m zglab_rag.evaluation.reranker_compare \
  --candidate-k 10 --candidate-k 20 --candidate-k 30 \
  --reranker-model-path runtime/models/mmarco-mMiniLMv2-L12-H384-v1
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

FTS5 使用 `tokenize='trigram'`。lexical profile 固定记录 tokenizer、config version 与 BM25
列权重 `title/section/content = 1/1/1`。FTS5 `bm25()` raw value 越小越好；对外 lexical
score 定义为 `-raw_bm25`，仅在 lexical 模式内比较。小于 3 个 Unicode 字符且没有可检索
term 的查询会返回 `lexical_not_applicable`，Hybrid 此时只使用 vector。

Hybrid 默认候选池各 50，RRF 参数为 `k=60`、`w_vector=w_lexical=1`，同分时依次使用最佳
单路 rank 与 `chunk_id`。RRF、cosine 与 raw BM25 不可跨模式比较。

Reranker passage 使用唯一的通用格式 `Title + Section + content`，CrossEncoder 按
`(query, passage)` pair 输出 higher-is-better relevance score。正式结果不会覆盖 vector score；
同分时按 original vector rank、再按 `chunk_id` 排序。candidate_k 仅允许 10/20/30，主要
baseline 为 20。指定模型的 471 MB 权重通过 Hugging Face 镜像下载到 Git ignored 的
`runtime/models/`，SHA-256 校验通过；没有替换模型、Embedding、chunking 或评测集。

真实 benchmark 选择 candidate_k=20：与相同候选集的 Vector 排序相比，Recall@1/3/5
分别从 0.5213/0.6809/0.7872 提升到 0.6809/0.7872/0.8404，MRR 从 0.6532 提升到
0.7753，Recall@20 保持 0.9255。candidate_k=10 较快但质量较低；30 的延迟更高且 MRR
低于 20。CPU 上 20 候选的重排中位延迟约 1.74 秒、模型加载约 2.70 秒、完整评测进程
峰值 RSS 约 1.49 GB。质量验收通过，但当前 2C2G 生产预算偏紧，默认仍使用 Vector。
完整指标、promotion/demotion、hard negatives、资源数据和 9 条人工 Query 记录见
[`docs/evaluations/phase-7-reranker.md`](docs/evaluations/phase-7-reranker.md)。

## Grounded Generation

Phase 8 的问答闭环通过 generation CLI 使用，默认 retrieval mode 仍为 `vector`：

```bash
uv run python -m zglab_rag.generation.cli ask \
  "Agent 长期记忆和 Context 有什么区别？"

uv run python -m zglab_rag.generation.cli ask \
  "你是谁？" --mode reranked --debug
```

LLM 通过 OpenAI-compatible endpoint 配置，真实 secret 只写在 Git ignored 的 `.env`：

```bash
ZGLAB_RAG_LLM_BASE_URL=https://...
ZGLAB_RAG_LLM_API_KEY=...
ZGLAB_RAG_LLM_MODEL=...
```

未配置时 CLI 输出 `Generation provider not configured`，不产生 stack trace。回答采用
claim-level citation：LLM 只能引用本次分配的短 Evidence ID（E1、E2…），每个 claim 必须
带有效 citation，cited evidence 集合由 validated claim citations 并集确定性生成；最终
用户可见回答由 validated claims 确定性渲染，provider 的 free-form answer 只作内部
信息，不能绕过校验。Citation 格式、归属、覆盖率与 insufficient-evidence 规则由确定性
校验器检查；校验失败最多修复重试 1 次，仍失败则安全返回拒答文本。检索为空或模型判定
证据不足时回答“当前公开知识库中没有足够信息回答这个问题”，不设置基于 score 的拒答
阈值。完整设计（Persona 边界、Prompt 注入边界、失败模型）见
[`docs/generation-grounding.md`](docs/generation-grounding.md)。

Generation 评测使用独立数据集，不修改 retrieval 评测集：

```bash
uv run python -m zglab_rag.evaluation.generation
uv run python -m zglab_rag.evaluation.generation --retrieval-only
```

确定性指标包括 retrieval evidence hit、citation validity、citation coverage、
should-answer correctness 与 insufficient-evidence correctness；hard negative 记录检索
证据、score、生成决定与引用。结果写入 Git ignored 的 `artifacts/evaluation/`。

## 安全边界

默认禁止进入公网知识库：

- 公司内部仓库、客户资料、合同原文、内部接口文档；
- Token、密码、SSH Key、Cookie、API Key；
- 私人聊天与未确认事实；
- 未明确允许公开的 private repository 内容；
- 模型缓存、索引数据库、日志与临时文件。

详见 `docs/knowledge-model.md` 与 `config/sources.yaml`。
