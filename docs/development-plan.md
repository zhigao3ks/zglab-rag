# Development Plan

This roadmap is intended for incremental Codex-assisted development. Each phase should stay independently testable and should not pre-implement later complexity.

## Phase 0 — Architecture Foundation

Status: scaffolded.

Deliverables:

- source registry;
- public/private boundary;
- domain metadata;
- replaceable AI component contracts;
- FastAPI health/source endpoints;
- Codex repository rules.

Acceptance:

```bash
uv sync
uv run pytest
uv run ruff check .
uv run uvicorn zglab_rag.api.main:app --reload
```

## Phase 1 — Local Markdown Ingestion

Status: implemented.

Goal: index `knowledge/identity/profile.md` without any GitHub synchronization or model dependency.

Implement:

- frontmatter parser;
- Markdown document loader;
- heading-aware chunker;
- deterministic document/chunk IDs;
- SHA-256 content hashes;
- unit tests.

Acceptance:

- profile loads into `KnowledgeDocument`;
- chunks preserve heading hierarchy;
- repeated ingestion produces stable IDs;
- visibility remains `public`.

Do not add a vector database yet unless needed for the next phase.

## Phase 2 — Git Knowledge Source Adapter

Status: implemented.

Goal: acquire configured Markdown files from selected local Git repository checkouts.

Start with:

- `notes`;
- `zglab-website`.

Implement:

- project-root-relative local repository paths;
- include/exclude filtering;
- source revision (commit SHA);
- source URL provenance.

Repository synchronization is intentionally outside this phase. The adapter does not clone, pull,
fetch or modify source repositories.

Acceptance:

- only configured files are loaded;
- private/unregistered repositories cannot be discovered automatically;
- repeated discovery and ingestion are deterministic.

## Phase 3 — Embedding Benchmark

Status: implemented.

Goal: choose the embedding implementation using real ZGLab documents.

Candidates should be benchmarked rather than assumed.

Compare at least:

- local CPU path;
- local GPU path on WSL where useful;
- optional ONNX/quantized path for production.

Record:

- model memory;
- index dimension;
- query latency;
- document embedding throughput;
- retrieval quality on a small golden dataset.

Only after this phase set `EMBEDDING_MODEL` as the default.

## Phase 4 — Vector Retrieval

Status: implemented as persistent vector storage and incremental index lifecycle.

Goal: first usable semantic search.

Implement:

- SQLite canonical metadata store with explicit schema version;
- pinned sqlite-vec vec0 adapter for the active BGE 512-dimensional profile;
- deterministic embedding profile and exact contextual input hashes;
- source-scoped new/changed/unchanged/deleted planning;
- transaction-safe incremental build, explicit rebuild and failed-run audit;
- public-only CLI vector KNN smoke search with metadata join.

Initial storage direction: SQLite + replaceable lightweight vector layer.

Acceptance:

- repeated identical build embeds zero chunks;
- changed/new/deleted fixture updates only affected rows;
- embedding failure leaves the previous complete index usable;
- persisted vectors survive database reopen and map to canonical chunks;
- private visibility cannot be returned by the public smoke search.

Formal `/search` API and production Retriever composition remain deferred. Phase 4 does not add
BM25, hybrid fusion, reranking, generation or source synchronization.

## Phase 5 — Hybrid Retrieval

Goal: improve exact technical term / project name retrieval.

Implement:

- BM25 lexical index;
- vector + lexical parallel retrieval;
- deterministic fusion (e.g. RRF or evaluated alternative);
- retrieval metrics.

Acceptance:

- compare vector-only vs BM25-only vs hybrid on the same golden set;
- retain benchmark output.

## Phase 6 — Lightweight Reranker

Goal: improve Top-K ordering after hybrid recall.

Implement via the `Reranker` contract.

Benchmark:

- no reranker;
- candidate lightweight local rerankers;
- CPU latency and peak memory on the target 2C2G server profile.

The production reranker is optional if measured gain is not worth resource cost.

## Phase 7 — Grounded Answer Generation

Goal: public `/ask` endpoint.

Implement:

- context builder;
- persona rules separated from evidence;
- LLM provider adapter;
- insufficient-evidence behavior;
- source citations.

Acceptance:

- answer uses first person naturally;
- factual claims are grounded in selected chunks;
- unknown questions do not trigger fabricated personal facts.

## Phase 8 — Evaluation Harness

Create a versioned golden dataset containing questions such as:

- identity queries;
- project queries;
- exact technical-term queries;
- cross-source questions;
- insufficient-evidence questions;
- adversarial private-data questions.

Track retrieval and generation separately.

## Phase 9 — Incremental Sync & Production Deployment

Implement:

- Git source revision checks;
- changed-document reindex;
- systemd service;
- Nginx reverse proxy;
- runtime data under `/var/lib/zglab-rag/`;
- health checks and logs.

Do not put production index/database/model cache inside the Git checkout.

## Codex Task Rule

When asking Codex to implement a phase, keep one phase or one vertical slice per task.

A good task includes:

```text
Goal
Files allowed to change
Contracts that must remain stable
Tests required
Explicit non-goals
Acceptance command
```

Avoid prompts such as “finish the whole RAG system”. The project is intentionally designed so each component can be implemented, measured and reviewed separately.
