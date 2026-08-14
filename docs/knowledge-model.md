# Knowledge Model

## 1. Purpose

This document defines the minimum metadata carried by every document and chunk in ZGLab RAG.

The goal is to make retrieval aware of identity, project, knowledge scope, freshness, provenance and public/private boundaries.

## 2. Document Metadata

Recommended document-level fields:

```yaml
document_id: string
source_id: string
source_kind: local | git | web | generated
scope: identity | project | knowledge | experience | dynamic
visibility: public | private
priority: integer
path: string
title: string
summary: string | null
tags: [string]
project: string | null
language: zh-CN | en | mixed
source_url: string | null
source_revision: string | null
content_hash: string
created_at: datetime | null
updated_at: datetime | null
ingested_at: datetime
```

### document_id

Stable logical identifier for a source document.

Prefer deterministic IDs based on source and path rather than random UUIDs when the same source is repeatedly synchronized.

Example:

```text
notes:knowledge/agent-long-term-memory.md
```

### source_id

Must match one entry in `config/sources.yaml`.

Examples:

```text
identity-profile
notes
zglab-website
resume-tailor
```

### scope

Defines the semantic role of a document.

#### identity

Stable personal facts and positioning.

#### project

Project implementation, architecture and design decisions.

#### knowledge

Reusable technical knowledge, problem reviews and methodology.

#### experience

Education, internship, publications, awards and other factual experience.

#### dynamic

Frequently changing source material.

### visibility

Hard security boundary.

For the public assistant:

```text
visibility must equal public before ranking/context construction
```

Visibility is not a soft ranking feature.

### priority

Source authority hint, not a substitute for relevance.

Suggested initial scale:

```text
100  canonical identity facts
90   official project docs / website structured data
80   curated Notes
70   other public project docs
```

Priority can be used as a tie-breaker or routing hint, not to force irrelevant identity chunks above relevant project evidence.

## 3. Chunk Metadata

Each chunk should retain:

```yaml
chunk_id: string
document_id: string
source_id: string
scope: string
visibility: string
priority: integer
title: string
section_path: [string]
chunk_index: integer
content: string
content_hash: string
token_count: integer | null
char_count: integer
project: string | null
tags: [string]
source_url: string | null
source_path: string
revision: string | null
```

`visibility`, `scope` and core provenance fields are duplicated intentionally on chunks so retrieval can filter before expensive ranking/context construction.

`chunk_id` is derived deterministically from document identity, heading path, section occurrence,
oversized-section part index and chunk content hash. Re-ingesting unchanged content therefore
produces the same IDs. `revision` carries the source revision when one exists; local curated files
may leave it null.

## 4. Markdown-aware Chunking

Do not start with blind fixed-character slicing.

Preferred logic:

1. parse frontmatter;
2. split by Markdown headings;
3. preserve heading hierarchy as `section_path`;
4. keep short adjacent sections together when appropriate;
5. split oversized sections with overlap;
6. never detach a chunk from its title / section context.

Example source:

```markdown
# Agent Memory

## Working Memory
...

## Long-Term Memory
...
```

Chunk should carry:

```yaml
title: Agent Memory
section_path:
  - Long-Term Memory
```

The retrieval text may internally prepend title/section context, while the stored `content` remains source-faithful.

## 5. Frontmatter

Curated local documents may optionally use:

```yaml
---
title: Example
scope: knowledge
visibility: public
tags:
  - RAG
  - Agent
project: null
---
```

Source registry values provide defaults. Document frontmatter may enrich metadata, but it must not silently escalate visibility from private to public.

Recommended rule:

```text
final visibility = most restrictive(source visibility, document visibility)
```

## 6. Provenance

Every answer-worthy chunk must be traceable back to a source document.

At minimum preserve:

- source ID;
- document path;
- source URL when available;
- commit SHA/revision for Git sources when available;
- section path.

The final answer citation layer should never invent a source label that cannot be mapped back to this metadata.

## 7. Conflict Handling

When documents disagree:

1. preserve both facts during ingestion;
2. do not rewrite sources to make them agree;
3. compare scope, priority, source authority and freshness at retrieval/generation time;
4. prefer canonical structured identity/project sources for current status;
5. explicitly acknowledge unresolved conflicts when evidence is insufficient.

Example:

```text
old project note: status = building
new official project metadata: status = completed
```

The newer official project source should normally win for current-state questions, while the old note remains useful for historical questions.

## 8. Source Types

### Local curated knowledge

Examples:

- `knowledge/identity/profile.md`;
- future curated experience documents.

These are high-authority sources maintained specifically for the assistant.

### Git repository docs

Only configured include patterns are eligible.

Good candidates:

- README;
- `docs/**/*.md`;
- architecture/design Markdown;
- selected project knowledge files.

Bad defaults:

- source code;
- lock files;
- generated files;
- binaries;
- `.env`;
- tests/fixtures unless explicitly useful.

## 9. Public Assistant Answer Contract

For a factual answer, the generation layer should receive:

```text
question
persona rules
selected public evidence
source metadata
```

It should not receive unrelated private documents and should not use profile/persona text as permission to invent details.

If evidence is insufficient, the expected behavior is a bounded response such as:

```text
我目前公开的资料里没有足够信息确认这一点。
```

rather than speculation.
