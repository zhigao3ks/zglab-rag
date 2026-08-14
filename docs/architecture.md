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

The first implementation may start with vector-only retrieval, but interfaces must leave room for hybrid retrieval and reranking.

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
├── sources/
│   └── local/Git source adapters and registry
├── ingestion/
│   └── normalize, parse, chunk, embed, index
├── retrieval/
│   └── lexical/vector/fusion/rerank
└── generation/
    └── context construction and grounded answer generation
```

Dependencies should point inward toward domain contracts rather than coupling the domain to FastAPI or a specific AI framework.

## 7. Storage

Initial target:

```text
SQLite
├── documents
├── chunks
├── source state
└── ingestion state

Vector layer
└── sqlite-vec or another lightweight replaceable index
```

The repository must not assume that sqlite-vec is permanent. Vector access should stay behind a repository/index interface.

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

A later sync process can therefore perform:

```text
source revision unchanged
→ skip

file hash unchanged
→ skip

changed file
→ delete/replace only affected chunks
```

instead of rebuilding the whole knowledge base every time.

## 11. Evaluation Architecture

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

## 12. Non-goals for v0

Do not implement yet:

- autonomous multi-agent orchestration;
- codebase-wide source code RAG;
- private knowledge mode;
- Elasticsearch / Milvus;
- Redis/Celery unless a real need appears;
- local large language model serving;
- automatic ingestion of every accessible GitHub repository.

These are expansion points, not requirements for the first usable system.
