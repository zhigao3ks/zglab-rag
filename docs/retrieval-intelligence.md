# Retrieval Intelligence

Phase 16 adds three opt-in Personal retrieval modes while preserving `vector` as the server
default. `hierarchical` performs deterministic FTS selection from document profiles to sections
and then returns matching indexed chunks. If no document candidate exists it falls back to the
normal lexical route. `graph` resolves exact canonical aliases, traverses the SQLite graph within
fixed bounds, and hydrates only real PUBLIC chunks named by edge provenance. It deliberately
returns no results when no entity is resolved.

`intelligent` runs Hybrid once, Hierarchical once, and Graph once, deduplicates by `chunk_id`, and
uses weighted reciprocal-rank fusion. Hybrid owns the only vector route, so each intelligent query
performs at most one BGE query encode. Component ranks and the final fusion score remain available
in retrieval metadata. Ties are ordered by score descending, best component rank, then chunk ID.

The mode is selected by `ZGLAB_RAG_GENERATION_RETRIEVAL_MODE`; supported values are `vector`,
`hybrid`, `reranked`, `hierarchical`, `graph`, and `intelligent` (lexical remains available to the
retrieval CLI and benchmark). Candidate counts,
weights, RRF constant, hop/node/edge limits, and top-k are bounded settings documented in the env
examples. Session resource fingerprints include the selected mode configuration plus structure
version/snapshot, preventing reuse across incompatible graph or hierarchy builds. Phase 15D cache
hits still skip retrieval only; generation and citations are rebuilt for every turn.

All new paths retain the Evidence boundary: profiles, sections, graph nodes, and graph edges are
routing metadata, never answer evidence. Returned evidence is always a persisted indexed chunk.
The server applies PUBLIC visibility and caller filters inside SQLite before hydration.

The benchmark command is:

```bash
uv run python -m zglab_rag.evaluation.retrieval_compare \
  --database /path/to/local-knowledge-copy.db \
  --dataset evaluation/retrieval-phase16.yaml \
  --phase16-json reports/phase16-retrieval-evaluation.json \
  --phase16-markdown docs/phase16-retrieval-evaluation.md
```

The committed benchmark keeps vector as the default; changing production mode requires separate
production acceptance.
