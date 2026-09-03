# Session Resource Reuse (Phase 15D)

Phase 15D adds a small persistent Session Workspace to `conversation.db`.
It is not long-term memory, a general cache, a vector store, or a public API.
The API constructs it only after authentication and owner/conversation binding;
requests without a conversation keep the existing single-turn path.

## Storage and limits

Schema v3 adds `session_resources`. Each row is one of `PERSONAL_RETRIEVAL`,
`WEB_EVIDENCE`, or `TOOL_RESULT`, has a deterministic SHA-256 key, JSON payload
and provenance, producer fingerprint, request id, expiry, and last-used time.
The v2-to-v3 migration is transactional; deletion of a conversation cascades
to its resources. Defaults are 48 rows, 512 KiB per conversation and 64 KiB per
item. Insertions purge expiry first and then evict deterministic LRU order
(`last_used_at`, `created_at`, `id`). Oversized bundles are not partially saved.

Defaults remain opt-in: `ZGLAB_RAG_SESSION_RESOURCE_REUSE_ENABLED=false`.
TTL is Personal 6h, Web 5m, Tool 24h.

## Keys, provenance, and invalidation

Keys use NFKC text normalization, whitespace collapsing, lowercasing, canonical
JSON, and SHA-256. Raw questions and arguments are never DB keys or logs.

- Personal includes effective retrieval query, mode, top-k, retrieval config,
  and the latest completed `index_runs` snapshot fingerprint. A successful
  knowledge sync changes that fingerprint, causing a conservative miss.
- Web includes effective research query, search provider, and policy/config
  fingerprint. Stored provenance preserves real URL, canonical URL, domain,
  retrieval time, search result URL, and redirect chain.
- Tool includes allowlisted tool id, canonical arguments, and format version.

All reads remain owner- and conversation-scoped. Expired, malformed, unknown
version, fingerprint-mismatched, invalid visibility, or SQLite-failed entries
are cache misses; the original capability executes normally.

## Execution boundaries

Personal hits hydrate only public `RetrievalResult` values and still run current
context building, generation, and citation validation. Web hits hydrate
`ExternalEvidence`, regenerate current-request W/E IDs, then run current
generation and validation. Tool hits occur only after the executor validates the
allowlist and plan input; they create a new request-local Tool Observation.

`ToolResult != Evidence`: tool output never becomes an Evidence ID,
AnswerSource, or citation. Web content remains untrusted web evidence. No cache
entry changes auth, CSRF, quota, concurrency, routing, planner output, step
limits, dependencies, or deadlines. Cache hits only avoid the underlying
retrieval/search-fetch/tool call; existing quota and concurrency gates remain.

There is no resource listing, mutation, upload, or reuse control endpoint, and
no frontend cache panel. Generic user-visible artifact storage is intentionally
deferred until a real typed producer exists.
