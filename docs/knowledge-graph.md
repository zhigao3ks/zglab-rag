# Provenance-backed Knowledge Graph

The Phase 16 graph is a small relational index in `knowledge.db`; it introduces no graph service
or resident model. Schema v3 stores `document_profiles`, deterministic `sections`, their FTS5
catalogs, and `graph_nodes`, `graph_aliases`, and `graph_edges`.

Node types are deliberately bounded to PROJECT, DOCUMENT, SECTION, PERSON, TECHNOLOGY, and TOPIC.
Relations are CONTAINS, MENTIONS, RELATED_TO, USES, DEVELOPED, and WORKED_ON. Chunks do not become
nodes: they remain provenance and the final retrieval target.

Edges accept only these provenance kinds:

- STRUCTURAL is derived from the indexed source/document/section hierarchy.
- TEXT_MENTION is created by NFKC normalization plus deterministic exact alias matching and points
  to the matching PUBLIC chunk. A mention never implies USES.
- CURATED comes from `config/knowledge-graph.yaml` and is accepted only when its source, document,
  section, or chunk selector resolves to a real PUBLIC indexed chunk. Missing/invalid provenance
  rejects the relation.

Aliases are NFKC-normalized, lower-cased, and whitespace-collapsed, then resolved longest-first.
There is no fuzzy or LLM entity resolution. Traversal is deterministic and bounded by start nodes,
hops, visited nodes, edges, and candidate documents (defaults: 8, 2, 24, 64, and 12). A query with
no canonical entity produces an empty graph route.

Profiles, sections, FTS rows, and graph rows are derived state. Every successful knowledge sync
rebuilds them in the same transaction as document/chunk/vector changes and records a structure
version, snapshot, and index run. Rollback therefore preserves the previous coherent snapshot;
changed or deleted documents cannot leave stale graph provenance. Only PUBLIC documents enter the
derived structures.

The v2-to-v3 migration is an explicit `BEGIN IMMEDIATE` transaction. Every DDL statement and the
schema-version update execute through the connection API, followed by one commit; any failure
rolls back all v3 objects and leaves schema version 2. Future versions continue to fail closed.
