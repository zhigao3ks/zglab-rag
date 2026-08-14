# ZGLab Personal Knowledge Assistant — Architecture

## 1. System Positioning

ZGLab RAG is a public-facing personal knowledge assistant, not a generic chatbot and not a Notes-only search service.

The assistant should answer in first person where appropriate, while grounding factual claims in approved public sources.

```text
Visitor
  ↓
Question
  ↓
Intent / Scope Hint
  ↓
Public Knowledge Retrieval
  ↓
Evidence Selection
  ↓
Context Construction
  ↓
LLM Generation
  ↓
First-person Answer + Sources
```

## 2. Knowledge Layers

The knowledge base is logically divided into five layers.

### Identity

Stable, high-confidence information about the person:

- profile;
- education;
- current technical directions;
- public contact links;
- long-term positioning.

Identity has high retrieval priority but should remain compact.

### Projects

Selected project knowledge:

- README;
- architecture docs;
- design decisions;
- project summaries;
- public project Notes.

Repository source code is not indexed by default.

### Knowledge

Reusable technical knowledge from Notes:

- `knowledge/`;
- `problems/`;
- `projects/`.

This layer represents technical understanding and engineering experience rather than identity facts.

### Experience

Publicly shareable structured experiences:

- internship;
- education detail;
- publications;
- awards;
- research / learning records.

This layer can be added incrementally.

### Dynamic Sources

Frequently changing information that may later be synchronized or fetched on demand:

- recent GitHub project state;
- recent Notes;
- website project status.

Dynamic sources must not silently override higher-confidence facts without freshness/conflict handling.

## 3. Source Registry

`config/sources.yaml` is the source-of-truth for what the system is allowed to ingest.

A source definition describes:

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

This enables configuration-driven ingestion instead of hard-coded repository handling.

For local Git sources, `local_path` resolves relative to the ZGLab RAG project root. The source
adapter validates that this path is a Git repository root, reads its current HEAD revision, and
discovers only allowlisted Markdown files. Exclude rules override include rules.

The acquisition layer does not synchronize repositories. Development and production deployment
tasks update local checkouts separately; ingestion only consumes their filesystem state. It never
scans sibling repositories that are absent from the registry.

## 4. Public Boundary

The initial product is public-facing. Therefore the default retrieval policy is:

```text
visibility = public
```

The following are not eligible for public ingestion unless explicitly reviewed and sanitized:

- company/internal repositories;
- client data;
- contract content;
- private messages;
- credentials;
- private repositories;
- personal sensitive information.

A future private mode must use a separate authenticated retrieval policy.

## 5. Domain Flow

### Ingestion Flow

```text
Source Registry
      ↓
Source Adapter
      ↓
Raw Document
      ↓
Normalize
      ↓
Markdown-aware Parse
      ↓
Chunk
      ↓
Metadata Enrichment
      ↓
Embedding
      ↓
Document Store + Vector Index
```

Important properties:

- deterministic source identity;
- content hash for change detection;
- stable document/chunk IDs where possible;
- visibility attached before indexing;
- raw source provenance preserved.

Phase 2 adapters implement the acquisition boundary as:

```text
registered local / local Git source
        ↓ inspect + deterministic discovery
RawDocument(source_path, revision, visibility, ...)
        ↓ existing Markdown parser and chunker
KnowledgeDocument + KnowledgeChunk
```

Only read-only Git inspection (`rev-parse` and `remote get-url`) is performed here. Clone, pull,
fetch, checkout and other synchronization operations belong to a future Sync Layer.

### Retrieval Flow

Target v2/v3 flow:

```text
Question
   ↓
Query Normalization
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

Phase 5 implements the vector branch. Phase 6 adds the independent FTS5/BM25 branch and RRF
fusion; reranking remains deferred to Phase 7.

The production vector path is:

```text
RetrievalQuery
→ active embedding profile validation
→ EmbeddingProvider.encode_queries
→ sqlite-vec cosine KNN rowid/distance candidates
→ relational public/source/scope filtering and metadata hydration
→ deterministic RetrievalResult ordering
```

Public visibility is mandatory and is applied in the relational hydration query. Controlled
over-fetch expands `candidate_k` when high-ranking disallowed rows would otherwise leave fewer than
`top_k` public results. Filtered rows expose no title, path or content to the retrieval layer or debug
output.

sqlite-vec cosine output is a distance. The public result contract defines
`score = 1 - cosine_distance`; higher score and lower distance both mean greater similarity.

The Phase 6 lexical path indexes separate `title`, stable `section_path` text and `content` columns
with FTS5 `tokenize='trigram'`. BM25 raw values are lower-is-better; unified lexical results expose
`score = -raw_bm25`, but Hybrid never blends this number with vector score. Query preparation quotes
literal terms so ordinary punctuation cannot become FTS syntax. Queries without a term of at least
three Unicode characters are explicitly lexical-not-applicable.

Hybrid uses configurable candidate pools (50 per branch in the production baseline) and combines
ranks using `1/(60 + rank)` with equal branch weights. Missing branches contribute zero; ties use the
best individual rank and then `chunk_id`. Relational public/source/scope filtering occurs inside each
branch before result metadata reaches fusion.

### Generation Flow

```text
Question
+
Selected Evidence
+
Identity / Persona Rules
        ↓
Context Builder
        ↓
LLM
        ↓
Answer
+
Source References
```

Persona must never become an evidence source.

## 6. Package Boundaries

```text
src/zglab_rag/
├── api/
│   └── HTTP protocol, request/response models
├── domain/
│   └── framework-independent entities and contracts
├── embeddings/
│   └── replaceable providers and model-specific query/document encoding
├── evaluation/
│   └── tracked retrieval datasets, in-memory ranking and benchmark orchestration
├── indexing/
│   └── embedding profile, incremental planning and atomic index lifecycle
├── storage/
│   └── SQLite schema/repositories and sqlite-vec adapter
├── sources/
│   └── local/Git source adapters and registry
├── ingestion/
│   └── normalize, parse, chunk, embed, index
├── retrieval/
│   └── production vector/lexical/hybrid contracts, filters and read-only retrieval
├── reranking/
│   └── replaceable providers, passage composition and Vector Top-N reranking
└── generation/
    └── context construction and grounded answer generation
```

Dependencies should point inward toward domain contracts rather than coupling the domain to FastAPI or a specific AI framework.

Phase 3 keeps benchmarking outside production retrieval. The benchmark composes either source-faithful
chunk content or title/section-enriched text, encodes queries and documents through separate provider
methods, and ranks the small corpus with in-memory cosine similarity. It does not persist embeddings or
introduce a vector database.

## 7. Storage

Phase 4/6 implementation:

```text
SQLite (canonical store)
├── source_snapshots
├── documents
├── chunks
├── embedding_profiles
├── lexical_profiles
├── chunk_embedding_state
└── index_runs

sqlite-vec vec0 (replaceable vector adapter)
└── rowid = chunks.id, embedding float[512] distance_metric=cosine

SQLite FTS5 trigram
└── fts_chunks(rowid = chunks.id, title, section_path, content)
```

The relational tables are authoritative for content, provenance and visibility. The vec0 table does
not duplicate chunk content or business metadata. sqlite-vec is pinned to `0.1.9`, loaded through
Python `sqlite3`, and checked with `vec_version()` whenever a database is opened. Vector access stays
behind the storage repository so sqlite-vec remains replaceable.

The database has an explicit schema version (currently version 2). The explicit v1→v2 migration
creates the lexical profile/table and backfills FTS directly from `chunks` in one transaction; it
does not rebuild documents or vectors. Unsupported versions and extension failures are errors; there
is no silent in-memory cosine fallback.

Production runtime data should live outside the code checkout, for example:

```text
/var/lib/zglab-rag/
├── knowledge.db
├── indexes/
├── models/
└── cache/
```

## 8. Model Deployment Strategy

### Development (WSL)

WSL is the primary development and model experiment environment.

It may run:

- local embedding models;
- local lightweight rerankers;
- CPU/GPU benchmark variants;
- retrieval evaluations.

### Production (2C2G server)

Production should prioritize low resident memory:

- FastAPI;
- SQLite;
- BM25 / lightweight vector index;
- lightweight local embedding if benchmarked successfully;
- lightweight local reranker only if memory/latency budget allows;
- external LLM API for final generation.

Model choice is an evaluation result, not an architectural constant.

## 9. API Direction

Initial public endpoints can converge toward:

```text
GET  /health
POST /ask
POST /search
GET  /sources
```

Possible later endpoints:

```text
POST /admin/reindex
POST /admin/sync
GET  /admin/index-status
```

Admin endpoints must not be publicly writable without authentication.

## 10. Incremental Indexing

Every document should retain at least:

- source ID;
- source revision / commit SHA when available;
- relative path;
- content hash;
- updated timestamp if available.

Phase 4 performs source-scoped planning with:

```text
compose_document_text(chunk, contextual)
→ SHA-256 exact embedding input
→ compare chunk_id + embedding_input_hash + embedding_profile_id
→ new / changed / unchanged / deleted
```

Only new and changed chunks are embedded. Deleted relational rows, embedding state and vec0 rows are
removed only for sources participating in the run. A title or section-path change alters the exact
contextual embedding input even when body content does not.

The active embedding profile deterministically records model ID/name, dimension, composition,
normalization, query mode and maximum length. Incompatible writes raise `IndexProfileMismatch`;
they never mix vectors. Replacing a profile requires an explicit full-scope rebuild.

Embedding runs before the apply transaction. After vector shape/finite-value validation, one short
transaction applies relational upserts, stale deletes, vectors, states, snapshots and run completion.
If embedding fails, the previous usable index remains unchanged and `index_runs` records the failure.

## 11. Production Vector Retrieval Evaluation

Phase 5 reuses `evaluation/retrieval.yaml` and the persistent Phase 4 index. It does not reconstruct
document embeddings or use the Phase 3 in-memory ranker. The same scored queries report target-level
Recall@K, query-level HitRate@K and MRR, including category breakdowns.

Hard-negative queries remain diagnostic only: their top-1 score/distance, top-2 score and margin are
recorded without selecting a refusal threshold. Query embedding, sqlite-vec search and total retrieval
latency are recorded separately; model load time is excluded from per-query latency.

The public retriever keeps its configured maximum top-k. The offline evaluator intentionally requests
the complete current-corpus ranking only for MRR parity with Phase 3, whose in-memory calculation did
not truncate rankings at Recall@30.

## 12. Lexical and Hybrid Retrieval Evaluation

Phase 6 runs vector-only, lexical-only and equal-weight RRF Hybrid against the unchanged 47 scored
queries and three hard negatives. Metrics, category breakdowns, per-mode score diagnostics and
mean/median/p95/max latency are written under ignored `artifacts/evaluation/`. A limited A/B comparison
of BM25 column weights selected `1/1/1` over `2/2/1` because it improved Recall@1 and MRR; no wider
hyperparameter search was performed. Current Hybrid quality is below the Phase 5 Vector baseline, so
the production CLI continues to default to vector.

## 13. Reranker Evaluation

Phase 7 adds an independent `RerankerProvider`; the provider scores `(query, passage)` pairs and does
not retrieve candidates. `RerankedRetriever` obtains only public/source/scope-filtered Vector Top-N,
composes one stable `Title + Section + content` passage, validates finite one-dimensional scores and
sorts descending. Equal scores retain deterministic original-rank then `chunk_id` ordering.

The primary configuration is
`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`, Torch/CPU, batch size 16 and candidate_k 20. Candidate
10, 20 and 30 are the bounded quality/latency experiment; 20 remains the primary baseline without a broad
parameter search. Results preserve original rank/vector score separately from rerank rank/relevance
score. Recall at the candidate cutoff is asserted invariant because re-ranking cannot add candidates.

The evaluator compares Vector and reranked rankings from the same candidate set, including category
deltas, promotion/demotion, hard negatives, split latency and process RSS. The exact configured
471 MB model was downloaded through a Hugging Face mirror into Git-ignored `runtime/models/` and its
SHA-256 was verified; no alternate model, dataset, embedding, composition or chunking was used.

On the 47-query dataset, candidate_k 20 improved Recall@1 from 0.5213 to 0.6809, Recall@3 from
0.6809 to 0.7872, Recall@5 from 0.7872 to 0.8404 and MRR from 0.6532 to 0.7753. Recall@20 remained
0.9255, confirming candidate-set invariance. Candidate 10 reached MRR 0.7480 with approximately
0.90 second median reranker latency; candidate 20 reached 0.7753 at 1.74 seconds; candidate 30 fell
to 0.7643 at 2.65 seconds. Candidate 20 is therefore the quality choice.

Category results were positive for identity, knowledge, problem and mixed, while project MRR fell
from 0.9333 to 0.8167. A manual query also exposed a generic README summary outranking the precise
Spring failure document. The full evaluation process peaked around 1.49 GB RSS. Phase 7 therefore
passes the quality experiment, but the 2C2G deployment budget is tight enough that production keeps
Vector as the default and exposes reranking only as an explicit mode.

## 14. Evaluation Architecture

Evaluation should be built as a first-class module rather than an ad-hoc script.

Dataset shape should support:

```text
question
expected source(s)
expected evidence
answer requirements
scope / category
```

Retrieval metrics may include:

- Recall@K;
- MRR;
- Hit Rate;
- reranker gain;
- latency.

Generation evaluation may later include:

- faithfulness;
- completeness;
- citation correctness;
- refusal / insufficient-evidence behavior.

## 15. Non-goals for v0

Do not implement yet:

- autonomous multi-agent orchestration;
- codebase-wide source code RAG;
- private knowledge mode;
- Elasticsearch / Milvus;
- Redis/Celery unless a real need appears;
- local large language model serving;
- automatic ingestion of every accessible GitHub repository.

These are expansion points, not requirements for the first usable system.
