# ZGLab Personal Knowledge Assistant — 架构设计

## 1. 系统定位

ZGLab RAG 是面向公网的个人知识助手，不是通用聊天机器人，也不是只用于搜索 Notes 的服务。

助手应在合适时使用第一人称回答，但所有事实性陈述都必须建立在经过批准的公开来源之上。

```text
访客
  ↓
问题
  ↓
意图 / Scope 提示
  ↓
公开知识检索
  ↓
证据选择
  ↓
Context 构建
  ↓
LLM 生成
  ↓
第一人称回答 + 来源
```

## 2. 知识分层

知识库在逻辑上划分为五层。

### Identity

关于个人的稳定、高置信度信息：

- Profile；
- 教育经历；
- 当前技术方向；
- 公开联系方式；
- 长期定位。

Identity 的检索优先级较高，但内容应保持精炼。

### Projects

经过选择的项目知识：

- README；
- 架构文档；
- 设计决策；
- 项目概述；
- 公开的项目 Notes。

默认不索引仓库源代码。

### Knowledge

来自 Notes 的可复用技术知识：

- `knowledge/`；
- `problems/`；
- `projects/`。

这一层表达技术理解和工程经验，而不是身份事实。

### Experience

允许公开分享的结构化经历：

- 实习；
- 教育细节；
- 论文；
- 奖项；
- 研究或学习记录。

这一层可以逐步补充。

### Dynamic Sources

未来可能需要同步或按需获取的频繁变化信息：

- 最近的 GitHub 项目状态；
- 最近的 Notes；
- 网站项目状态。

没有时效和冲突处理机制时，动态来源不得静默覆盖置信度更高的事实。

## 3. 来源注册表

`config/sources.yaml` 是系统允许 ingestion 内容的唯一事实来源。

一条来源配置描述：

```text
source id
source kind
scope
visibility
priority
location
include patterns
exclude patterns
```

这样可以通过配置驱动 ingestion，而不是硬编码仓库处理逻辑。

对于本地 Git 来源，`local_path` 相对于 ZGLab RAG 项目根目录解析。来源适配器会校验该路径
确实是 Git 仓库根目录，读取当前 HEAD revision，并且只发现 allowlist 中的 Markdown 文件。
exclude 规则优先于 include 规则。

获取层不负责同步仓库。开发和生产部署任务分别更新本地 checkout；ingestion 只消费文件系统
中的现有状态。系统不会扫描注册表之外的同级仓库。

## 4. 公开边界

初始产品面向公网，因此默认检索策略为：

```text
visibility = public
```

以下内容未经明确审核和脱敏，不得进入公网 ingestion：

- 公司或内部仓库；
- 客户数据；
- 合同内容；
- 私人消息；
- 凭据；
- 私有仓库；
- 个人敏感信息。

未来的 private 模式必须使用独立的鉴权检索策略。

## 5. Domain 数据流

### Ingestion 流程

```text
来源注册表
      ↓
来源适配器
      ↓
Raw Document
      ↓
Normalize
      ↓
Markdown 结构感知解析
      ↓
Chunk
      ↓
元数据补充
      ↓
Embedding
      ↓
Document Store + Vector Index
```

重要属性：

- 确定性的来源标识；
- 用于变化检测的内容哈希；
- 尽可能稳定的 document/chunk ID；
- 建立索引前绑定 visibility；
- 保留原始来源追踪信息。

Phase 2 适配器实现以下获取边界：

```text
已注册的 local / local Git source
        ↓ 检查 + 确定性发现
RawDocument(source_path, revision, visibility, ...)
        ↓ 现有 Markdown parser 和 chunker
KnowledgeDocument + KnowledgeChunk
```

这里只执行只读 Git 检查（`rev-parse` 和 `remote get-url`）。clone、pull、fetch、checkout
及其他同步操作属于未来的 Sync Layer。

### Retrieval 流程

目标 v2/v3 流程：

```text
问题
   ↓
Query Normalize
   ↓
┌───────────────┬────────────────┐
│ BM25          │ Vector Search  │
└───────┬───────┴────────┬───────┘
        │                │
        └───── Fusion ───┘
                 ↓
              Top N
                 ↓
             Reranker
                 ↓
              Top K
                 ↓
          Evidence / Context
```

Phase 5 实现 Vector 分支，Phase 6 增加独立的 FTS5/BM25 分支和 RRF 融合，Phase 7 增加
Vector Top-N 的 Reranking。

生产 Vector 路径为：

```text
RetrievalQuery
→ 校验当前 embedding profile
→ EmbeddingProvider.encode_queries
→ sqlite-vec cosine KNN rowid/distance 候选
→ 关系表 public/source/scope 过滤及元数据装配
→ 确定性的 RetrievalResult 排序
```

Public visibility 是强制条件，在关系表装配查询中完成过滤。当高排名的不允许数据导致
public 结果不足 `top_k` 时，受控 over-fetch 会扩大 `candidate_k`。被过滤行的 title、path
和 content 都不会暴露给 Retrieval 层或 debug 输出。

sqlite-vec cosine 的输出是 distance。公开结果契约定义 `score = 1 - cosine_distance`；
score 越高、distance 越低都表示相似度越高。

Phase 6 的 Lexical 路径使用 FTS5 `tokenize='trigram'`，分别索引 `title`、稳定的
`section_path` 文本和 `content` 列。BM25 原始值越低越好；统一的 Lexical 结果对外提供
`score = -raw_bm25`，但 Hybrid 不会将其与 Vector score 直接混合。Query preparation 会
引用字面 term，避免普通标点被解释为 FTS 语法。查询中不存在至少三个 Unicode 字符的 term
时，结果会明确标记为 lexical-not-applicable。

Hybrid 使用可配置候选池，生产 baseline 为每个分支 50 条，并以相同分支权重通过
`1/(60 + rank)` 融合排名。缺失分支贡献为零；同分时依次使用最佳单路 rank 和 `chunk_id`。
每个分支都在元数据进入 Fusion 前，通过关系表完成 public/source/scope 过滤。

### Generation 流程

Phase 8 实现的确定性闭环：

```text
问题
  ↓
Retrieval（vector 默认 / reranked 显式可选）
  ↓
RetrievalResult[]
  ↓
ContextBuilder：短 Evidence ID（E1…En）+ 预算截断 + 注入边界
  ↓
GenerationProvider（OpenAI-compatible）
  ↓
结构化 JSON（answer / claims / citations / insufficient_evidence）
  ↓
CitationValidator（确定性代码校验，最多 1 次修复重试）
  ↓
Evidence ID → chunk_id / source_path / section_path 映射
  ↓
GroundedAnswer + Sources
```

Persona 绝不能成为证据来源。Citation 合法性、归属、覆盖率、insufficient-evidence 规则与
private 边界都由代码校验，不只依赖 Prompt。检索为空、模型判定不足或校验无法安全恢复时，
返回固定拒答文本；不设置基于 score 的拒答阈值。

## 6. Package 边界

```text
src/zglab_rag/
├── api/
│   └── HTTP 协议、request/response model
├── domain/
│   └── 与框架无关的 entity 和 contract
├── embeddings/
│   └── 可替换 Provider，以及模型相关的 query/document encoding
├── evaluation/
│   └── 受版本管理的检索数据集、内存排序和 benchmark 编排
├── indexing/
│   └── Embedding profile、增量规划和原子索引生命周期
├── storage/
│   └── SQLite schema/repository 和 sqlite-vec adapter
├── sources/
│   └── local/Git source adapter 和注册表
├── ingestion/
│   └── normalize、parse、chunk、embed、index
├── retrieval/
│   └── 生产 Vector/Lexical/Hybrid contract、filter 和只读检索
├── reranking/
│   └── 可替换 Provider、passage composition 和 Vector Top-N 重排
└── generation/
    └── Evidence context、Persona、结构化生成、Citation 校验与问答编排
```

依赖应指向内部 Domain contract，不应让 Domain 耦合 FastAPI 或具体 AI 框架。

Phase 3 将 benchmark 放在生产 Retrieval 之外。Benchmark 使用忠实来源的 Chunk content，
或拼接 title/section 的文本；通过 Provider 的独立方法编码 query 和 document，并在小型语料上
使用内存 cosine similarity 排序。它不持久化 Embedding，也不引入 Vector DB。

## 7. 存储

Phase 4/6 实现：

```text
SQLite（权威存储）
├── source_snapshots
├── documents
├── chunks
├── embedding_profiles
├── lexical_profiles
├── chunk_embedding_state
└── index_runs

sqlite-vec vec0（可替换 Vector adapter）
└── rowid = chunks.id, embedding float[512] distance_metric=cosine

SQLite FTS5 trigram
└── fts_chunks(rowid = chunks.id, title, section_path, content)
```

关系表是 content、来源和 visibility 的权威存储。vec0 表不会复制 Chunk content 或业务元数据。
sqlite-vec 固定为 `0.1.9`，通过 Python `sqlite3` 加载，并在每次打开数据库时调用
`vec_version()` 检查。Vector 访问封装在 Storage repository 后面，使 sqlite-vec 保持可替换。

数据库具有显式 schema version，目前为 version 2。显式 v1→v2 migration 会创建 Lexical
profile/table，并在同一事务中直接从 `chunks` 回填 FTS，不重新构建 document 或 Vector。
不支持的版本和扩展加载失败均视为错误；系统不会静默回退到内存 cosine。

生产 runtime 数据应放在代码 checkout 外部，例如：

```text
/var/lib/zglab-rag/
├── knowledge.db
├── indexes/
├── models/
└── cache/
```

## 8. 模型部署策略

### 开发环境（WSL）

WSL 是主要开发和模型实验环境。

可以运行：

- 本地 Embedding 模型；
- 本地轻量 Reranker；
- CPU/GPU benchmark 变体；
- Retrieval evaluation。

### 生产环境（2C2G Server）

生产环境应优先降低常驻内存：

- FastAPI；
- SQLite；
- BM25 或轻量 Vector Index；
- 经 benchmark 验证的轻量本地 Embedding；
- 仅在内存和延迟允许时使用轻量本地 Reranker；
- 最终 Generation 使用外部 LLM API。

模型选择是评测结果，不是架构常量。

## 9. API 方向

首批公网接口可以收敛为：

```text
GET  /health
POST /ask
POST /search
GET  /sources
```

未来可能增加：

```text
POST /admin/reindex
POST /admin/sync
GET  /admin/index-status
```

Admin 接口未经鉴权不得允许公网写入。

## 10. 增量索引

每个文档至少应保留：

- source ID；
- source revision 或 commit SHA（如果有）；
- 相对路径；
- content hash；
- 更新时间（如果有）。

Phase 4 按来源执行以下规划：

```text
compose_document_text(chunk, contextual)
→ 对精确 Embedding input 计算 SHA-256
→ 比较 chunk_id + embedding_input_hash + embedding_profile_id
→ new / changed / unchanged / deleted
```

只对 new 和 changed Chunk 执行 Embedding。关系表行、Embedding state 和 vec0 row 的删除仅限
于本次运行涉及的来源。即使正文不变，title 或 section path 变化也会改变精确 contextual
Embedding input。

当前 Embedding profile 确定性记录 model ID/name、dimension、composition、normalization、
query mode 和 maximum length。不兼容写入会抛出 `IndexProfileMismatch`，绝不混合 Vector。
替换 profile 需要显式执行全量 rebuild。

Embedding 在 apply 事务之前执行。完成 Vector shape 和有限值校验后，一个短事务会应用关系表
upsert、过期数据删除、Vector、state、snapshot 和 run completion。如果 Embedding 失败，
之前可用的索引保持不变，`index_runs` 会记录失败。

## 11. 生产 Vector Retrieval 评测

Phase 5 复用 `evaluation/retrieval.yaml` 和 Phase 4 的持久化索引，不重建 document Embedding，
也不使用 Phase 3 的内存 Ranker。同一组计分 Query 会输出 target-level Recall@K、query-level
HitRate@K 和 MRR，包括 category breakdown。

Hard-negative Query 只用于诊断：记录 Top1 score/distance、Top2 score 和 margin，不选择拒答
阈值。Query Embedding、sqlite-vec search 和总 Retrieval latency 分别记录；模型 load time
不计入单次 Query latency。

Public Retriever 遵守配置的 maximum top-k。离线 Evaluator 只为保持与 Phase 3 的 MRR
计算一致而请求当前完整语料排名；Phase 3 的内存计算不会在 Recall@30 截断排名。

## 12. Lexical 与 Hybrid Retrieval 评测

Phase 6 在未修改的 47 条计分 Query 和 3 条 hard negatives 上运行 Vector-only、Lexical-only
和等权 RRF Hybrid。指标、category breakdown、各模式 score diagnostics，以及
mean/median/p95/max latency 写入 Git ignored 的 `artifacts/evaluation/`。有限的 BM25 列权重
A/B 比较选择 `1/1/1`，而不是 `2/2/1`，因为前者改善了 Recall@1 和 MRR；没有执行更广泛的
参数搜索。当前 Hybrid 质量低于 Phase 5 Vector baseline，因此生产 CLI 继续默认使用 Vector。

## 13. Reranker 评测

Phase 7 增加独立的 `RerankerProvider`。Provider 只对 `(query, passage)` pair 评分，不负责
获取候选。`RerankedRetriever` 只接收完成 public/source/scope 过滤的 Vector Top-N，构造
唯一稳定的 `Title + Section + content` passage，校验一维有限 score，然后按分数降序排序。
同分时依次按原始 rank 和 `chunk_id` 确定顺序。

主配置为 `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`、Torch/CPU、batch size 16、
candidate_k 20。10、20、30 是有限的质量/延迟实验，20 为主要 baseline，不执行大规模参数
搜索。结果分别保留原始 rank/vector score 和 rerank rank/relevance score。由于重排不能新增
候选，candidate cutoff 处的 Recall 必须保持不变。

Evaluator 使用同一候选集比较 Vector 和 Reranked 排名，记录 category delta、
promotion/demotion、hard negatives、分段 latency 和进程 RSS。指定的 471 MB 模型已通过
Hugging Face 镜像下载到 Git ignored 的 `runtime/models/` 并完成 SHA-256 校验；没有替换模型、
数据集、Embedding、composition 或 chunking。

在 47 条 Query 数据集上，candidate_k 20 将 Recall@1 从 0.5213 提升到 0.6809，Recall@3
从 0.6809 提升到 0.7872，Recall@5 从 0.7872 提升到 0.8404，MRR 从 0.6532 提升到
0.7753。Recall@20 保持 0.9255，候选集不变量成立。Candidate 10 的 MRR 为 0.7480，
Reranker 中位延迟约 0.90 秒；Candidate 20 为 0.7753 和 1.74 秒；Candidate 30 降至
0.7643 和 2.65 秒。因此 Candidate 20 是质量最优选择。

Identity、Knowledge、Problem 和 Mixed 分类的结果为正向，Project MRR 从 0.9333 降至
0.8167。人工 Query 还发现泛化的 README 摘要超过精确 Spring 问题文档。完整评测进程峰值
约 1.49 GB RSS。因此 Phase 7 的质量实验成功，但 2C2G 部署预算仍然偏紧；生产保持 Vector
为默认模式，只显式提供 Reranking。完整记录见 `docs/evaluations/phase-7-reranker.md`。

## 14. Grounded Generation 与 Citation

Phase 8 冻结 Retrieval baseline（默认 Vector），在 `generation/` 建立确定性问答闭环：

- `ContextBuilder` 将已过滤的 RetrievalResult 转为带短 Evidence ID 的 EvidenceItem，只把
  Evidence ID、Title、Section 和 Content 交给 LLM；预算截断确定性保留高 rank 完整 chunk；
- Persona 与 Evidence 规则写在 system message，问题与 Evidence 写在 user message，
  Evidence 被明确标注为只读数据，防止 Prompt 注入获得系统优先级；
- `GenerationProvider` protocol 隔离厂商实现；第一版为 OpenAI-compatible HTTP Provider，
  网络重试留在 Provider 层，API Key 不进入日志、Prompt 或诊断；
- 输出为 claim-level citation 的结构化 JSON；`CitationValidator` 确定性检查格式、归属、
  覆盖率与 insufficient-evidence 规则，修复重试最多 1 次；
- `GroundedAnswerService` 是固定 workflow 而不是 Agent；失败模型区分 RetrievalFailure、
  ProviderFailure、InvalidStructuredOutput、CitationValidationFailure 与
  InsufficientEvidence；
- 独立评测集 `evaluation/generation.yaml` 与确定性指标（evidence hit、citation
  validity/coverage、should-answer 与 insufficient correctness），不引入 LLM Judge。

完整设计见 `docs/generation-grounding.md`。

## 15. Evaluation 架构

Evaluation 应作为一等模块建设，而不是临时脚本。

数据集结构应支持：

```text
question
expected source(s)
expected evidence
answer requirements
scope / category
```

Retrieval 指标可以包括：

- Recall@K；
- MRR；
- Hit Rate；
- Reranker gain；
- latency。

Generation evaluation 未来可以包括：

- faithfulness；
- completeness；
- citation correctness；
- refusal 或 insufficient-evidence behavior。

## 16. v0 暂不实现的内容

暂不实现：

- 自主 Multi-Agent 编排；
- 面向整个代码库的 Source Code RAG；
- private knowledge mode；
- Elasticsearch 或 Milvus；
- 没有真实需求时引入 Redis/Celery；
- 本地大语言模型服务；
- 自动 ingestion 所有可访问的 GitHub 仓库。

这些是未来扩展点，不是首个可用系统的必需条件。
