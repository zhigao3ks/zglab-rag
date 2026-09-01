"""Production orchestration for configured-source synchronization.

This module deliberately composes existing source acquisition, Markdown ingestion and
incremental indexing boundaries. It does not change their algorithms or discover any
unregistered source.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from zglab_rag.config import Settings
from zglab_rag.embeddings.config import EmbeddingModelConfig
from zglab_rag.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
from zglab_rag.indexing.indexer import KnowledgeIndexer, plan_sources
from zglab_rag.indexing.models import EmbeddingProfile, IndexPlan, IndexRunResult, SourceIndexInput
from zglab_rag.ingestion.chunking import ChunkingConfig, MarkdownHeadingChunker
from zglab_rag.ingestion.markdown import MarkdownDocumentParser
from zglab_rag.ingestion.pipeline import MarkdownSourceIngestionPipeline
from zglab_rag.sources.factory import create_source_adapter
from zglab_rag.sources.registry import SourceRegistry
from zglab_rag.storage.database import Database
from zglab_rag.storage.repositories import IndexRepository

SyncState = Literal["changed", "unchanged", "not_indexed"]


@dataclass(frozen=True, slots=True)
class SourceSyncPlan:
    """One configured source's observed and indexed state."""

    source_id: str
    current_revision: str | None
    indexed_revision: str | None
    state: SyncState
    plan: IndexPlan


@dataclass(frozen=True, slots=True)
class SyncPlan:
    """Read-only synchronization plan for all selected registered sources."""

    source_inputs: tuple[SourceIndexInput, ...]
    sources: tuple[SourceSyncPlan, ...]
    aggregate: IndexPlan

    @property
    def has_changes(self) -> bool:
        return any(source.state != "unchanged" for source in self.sources)


def acquire_sources(
    source_ids: list[str],
    *,
    sources_config: str | Path,
    project_root: str | Path,
    settings: Settings,
) -> list[SourceIndexInput]:
    """Acquire only explicitly selected, enabled registry sources."""
    registry = SourceRegistry.from_yaml(sources_config)
    chunker = MarkdownHeadingChunker(
        ChunkingConfig(
            target_size=settings.chunk_target_size,
            max_size=settings.chunk_max_size,
            overlap=settings.chunk_overlap,
        )
    )
    inputs: list[SourceIndexInput] = []
    for source_id in dict.fromkeys(source_ids):
        source = registry.get_enabled(source_id)
        pipeline = MarkdownSourceIngestionPipeline(
            create_source_adapter(
                source,
                project_root=project_root,
                source_checkout_root=settings.source_checkout_root,
            ),
            MarkdownDocumentParser(),
            chunker,
        )
        ingested = pipeline.ingest(source)
        inputs.append(
            SourceIndexInput(
                source=source,
                revision=ingested.revision,
                documents=ingested.documents,
                chunks=ingested.chunks,
            )
        )
    return inputs


def plan_sync(
    database: Database,
    source_inputs: list[SourceIndexInput],
    profile: EmbeddingProfile,
) -> SyncPlan:
    """Build a read-only source-by-source plan without changing the index."""
    if database.path.is_file():
        connection = database.connect(read_only=True, initialize=False)
        try:
            repository: IndexRepository | None = IndexRepository(connection)
            indexed_revisions = {
                str(row["source_id"]): None if row["revision"] is None else str(row["revision"])
                for row in repository.source_snapshots()
            }
            aggregate = plan_sources(repository, source_inputs, profile)
            source_plans = [
                _source_plan(repository, source, profile, indexed_revisions.get(source.source.id))
                for source in source_inputs
            ]
        finally:
            connection.close()
    else:
        aggregate = plan_sources(None, source_inputs, profile)
        source_plans = [
            SourceSyncPlan(
                source_id=source.source.id,
                current_revision=source.revision,
                indexed_revision=None,
                state="not_indexed",
                plan=plan_sources(None, [source], profile),
            )
            for source in source_inputs
        ]
    return SyncPlan(
        source_inputs=tuple(source_inputs),
        sources=tuple(source_plans),
        aggregate=aggregate,
    )


def apply_sync(
    database: Database,
    sync_plan: SyncPlan,
    profile: EmbeddingProfile,
    model_config: EmbeddingModelConfig,
    *,
    batch_size: int,
) -> IndexRunResult | None:
    """Apply a previously acquired plan using the existing atomic index transaction.

    Acquisition and embedding happen before the repository apply transaction. If either
    fails, the prior complete index remains usable; `KnowledgeIndexer` also marks the
    failed index run for audit.
    """
    if not sync_plan.has_changes:
        return None
    connection = database.connect()
    try:
        provider = None
        if sync_plan.aggregate.needs_embedding:
            provider = SentenceTransformerEmbeddingProvider(
                model_config,
                device="cpu",
                batch_size=batch_size,
            )
        return KnowledgeIndexer(
            connection,
            provider,
            profile,
            batch_size=batch_size,
        ).build(list(sync_plan.source_inputs))
    finally:
        connection.close()


def _source_plan(
    repository: IndexRepository,
    source: SourceIndexInput,
    profile: EmbeddingProfile,
    indexed_revision: str | None,
) -> SourceSyncPlan:
    plan = plan_sources(repository, [source], profile)
    revision_changed = source.revision != indexed_revision
    state: SyncState
    if not repository.source_snapshots() or source.source.id not in {
        str(row["source_id"]) for row in repository.source_snapshots()
    }:
        state = "not_indexed"
    elif revision_changed or plan.new or plan.changed or plan.deleted:
        state = "changed"
    else:
        state = "unchanged"
    return SourceSyncPlan(
        source_id=source.source.id,
        current_revision=source.revision,
        indexed_revision=indexed_revision,
        state=state,
        plan=plan,
    )
