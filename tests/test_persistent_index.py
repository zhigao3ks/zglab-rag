from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from zglab_rag.domain.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    Scope,
    SourceDefinition,
    SourceKind,
    Visibility,
)
from zglab_rag.embeddings.config import (
    EmbeddingBackend,
    EmbeddingModelConfig,
    QueryMode,
)
from zglab_rag.evaluation.composition import TextComposition
from zglab_rag.indexing.errors import (
    EmbeddingValidationError,
    IndexProfileMismatch,
    RebuildScopeError,
)
from zglab_rag.indexing.indexer import KnowledgeIndexer, plan_sources
from zglab_rag.indexing.models import (
    EmbeddingProfile,
    SourceIndexInput,
    StoredChunkState,
)
from zglab_rag.indexing.planner import build_index_plan, embedding_input_hash
from zglab_rag.indexing.search import search_public_vectors
from zglab_rag.storage.database import Database
from zglab_rag.storage.errors import SchemaVersionError
from zglab_rag.storage.repositories import IndexRepository
from zglab_rag.storage.schema import SCHEMA_VERSION, VECTOR_DIMENSION


def _model_config(**updates) -> EmbeddingModelConfig:
    values = {
        "id": "bge-small-zh-v1.5",
        "model_name": "BAAI/bge-small-zh-v1.5",
        "backend": EmbeddingBackend.SENTENCE_TRANSFORMERS,
        "query_mode": QueryMode.BGE_ZH_INSTRUCTION,
        "normalize": True,
        "max_length": 512,
        "enabled": True,
    }
    values.update(updates)
    return EmbeddingModelConfig(**values)


def _profile(**config_updates) -> EmbeddingProfile:
    return EmbeddingProfile.create(
        _model_config(**config_updates),
        dimension=VECTOR_DIMENSION,
        composition=TextComposition.CONTEXTUAL,
    )


def _source(source_id: str = "notes", visibility=Visibility.PUBLIC) -> SourceDefinition:
    return SourceDefinition(
        id=source_id,
        kind=SourceKind.LOCAL,
        scope=Scope.KNOWLEDGE,
        visibility=visibility,
        priority=80,
        path=f"knowledge/{source_id}.md",
        include=[f"knowledge/{source_id}.md"],
    )


def _document(
    source_id: str = "notes",
    *,
    title: str = "Document",
    visibility=Visibility.PUBLIC,
) -> KnowledgeDocument:
    content = "document content"
    return KnowledgeDocument(
        document_id=f"{source_id}:knowledge/{source_id}.md",
        source_id=source_id,
        source_kind=SourceKind.LOCAL,
        scope=Scope.KNOWLEDGE,
        visibility=visibility,
        priority=80,
        path=f"knowledge/{source_id}.md",
        title=title,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
    )


def _chunk(
    chunk_id: str,
    *,
    source_id: str = "notes",
    content: str = "alpha",
    title: str = "Document",
    section_path: list[str] | None = None,
    visibility=Visibility.PUBLIC,
    chunk_index: int = 0,
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        document_id=f"{source_id}:knowledge/{source_id}.md",
        source_id=source_id,
        scope=Scope.KNOWLEDGE,
        visibility=visibility,
        priority=80,
        title=title,
        section_path=section_path or ["Section"],
        chunk_index=chunk_index,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        char_count=len(content),
        source_path=f"knowledge/{source_id}.md",
    )


def _input(
    chunks: list[KnowledgeChunk],
    *,
    source_id: str = "notes",
    visibility=Visibility.PUBLIC,
) -> SourceIndexInput:
    return SourceIndexInput(
        source=_source(source_id, visibility),
        revision="revision-1",
        documents=[_document(source_id, visibility=visibility)],
        chunks=chunks,
    )


class FakeEmbeddingProvider:
    model_name = "BAAI/bge-small-zh-v1.5"
    dimension = VECTOR_DIMENSION
    device = "cpu"

    def __init__(self, *, fail_on_call: int | None = None, invalid_shape: bool = False) -> None:
        self.document_calls = 0
        self.document_texts: list[str] = []
        self.fail_on_call = fail_on_call
        self.invalid_shape = invalid_shape

    def encode_documents(self, texts):
        self.document_calls += 1
        if self.document_calls == self.fail_on_call:
            raise RuntimeError("simulated embedding failure")
        self.document_texts.extend(texts)
        vectors = np.stack([self._vector(text) for text in texts])
        return vectors[:, :-1] if self.invalid_shape else vectors

    def encode_queries(self, texts):
        return np.stack([self._vector(text) for text in texts])

    @staticmethod
    def _vector(text: str) -> np.ndarray:
        vector = np.zeros(VECTOR_DIMENSION, dtype=np.float32)
        if "alpha" in text:
            vector[0] = 1.0
        elif "beta" in text:
            vector[1] = 1.0
        else:
            vector[2] = 1.0
        return vector


@pytest.fixture
def database(tmp_path: Path) -> Database:
    return Database(tmp_path / "knowledge.db")


def test_database_initializes_sqlite_vec_and_explicit_schema(database: Database) -> None:
    connection = database.connect()
    versions = database.versions(connection)
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        )
    }
    connection.close()

    assert versions.sqlite == sqlite3.sqlite_version
    assert versions.sqlite_vec == "v0.1.9"
    assert versions.schema == SCHEMA_VERSION
    assert {
        "source_snapshots",
        "documents",
        "chunks",
        "embedding_profiles",
        "chunk_embedding_state",
        "index_runs",
        "vec_chunks",
    } <= tables


def test_database_rejects_unsupported_schema_version(database: Database) -> None:
    connection = database.connect()
    connection.execute(
        "UPDATE schema_metadata SET value='999' WHERE key='schema_version'"
    )
    connection.close()

    with pytest.raises(SchemaVersionError, match="Unsupported database schema version"):
        database.connect()


@pytest.mark.parametrize(
    "update",
    [
        {"model_name": "other/model"},
        {"normalize": False},
        {"query_mode": QueryMode.E5_PREFIX},
        {"max_length": 256},
    ],
)
def test_embedding_profile_hash_is_deterministic_and_config_sensitive(update) -> None:
    assert _profile() == _profile()
    assert _profile().profile_id != _profile(**update).profile_id
    assert _profile().config_hash != _profile(**update).config_hash


def test_embedding_profile_dimension_and_composition_are_hash_inputs() -> None:
    config = _model_config()
    baseline = _profile()
    other_dimension = EmbeddingProfile.create(
        config, dimension=384, composition=TextComposition.CONTEXTUAL
    )
    other_composition = EmbeddingProfile.create(
        config, dimension=VECTOR_DIMENSION, composition=TextComposition.CONTENT_ONLY
    )

    assert len(baseline.config_hash) == 64
    assert len({baseline.profile_id, other_dimension.profile_id, other_composition.profile_id}) == 3


def test_empty_database_plan_marks_every_chunk_new() -> None:
    chunks = [_chunk("a"), _chunk("b")]
    plan = build_index_plan(chunks, [], _profile())

    assert plan.statistics() == {
        "total": 2,
        "new": 2,
        "changed": 0,
        "unchanged": 0,
        "deleted": 0,
        "needs_embedding": 2,
    }


def test_identical_embedding_input_is_unchanged() -> None:
    chunk = _chunk("a")
    profile = _profile()
    text = f"Title: {chunk.title}\nSection: Section\n\n{chunk.content}"
    stored = StoredChunkState("a", "notes", profile.profile_id, embedding_input_hash(text))

    plan = build_index_plan([chunk], [stored], profile)

    assert [item.chunk.chunk_id for item in plan.unchanged] == ["a"]
    assert not plan.needs_embedding


@pytest.mark.parametrize(
    "changed",
    [
        _chunk("a", content="beta"),
        _chunk("a", title="Changed title"),
        _chunk("a", section_path=["Changed section"]),
    ],
)
def test_contextual_input_changes_are_detected(changed: KnowledgeChunk) -> None:
    original = _chunk("a")
    profile = _profile()
    initial = build_index_plan([original], [], profile).new[0]
    state = StoredChunkState("a", "notes", profile.profile_id, initial.embedding_input_hash)

    plan = build_index_plan([changed], [state], profile)

    assert [item.chunk.chunk_id for item in plan.changed] == ["a"]


def test_deleted_chunks_are_detected_only_from_supplied_source_state() -> None:
    profile = _profile()
    states = [
        StoredChunkState("stale", "notes", profile.profile_id, "hash"),
        StoredChunkState("identity", "identity-profile", profile.profile_id, "hash"),
    ]

    plan = build_index_plan([], states[:1], profile)

    assert [item.chunk_id for item in plan.deleted] == ["stale"]
    assert all(item.chunk_id != "identity" for item in plan.deleted)


def test_first_build_persists_metadata_vectors_profile_and_success_run(database: Database) -> None:
    connection = database.connect()
    provider = FakeEmbeddingProvider()
    result = KnowledgeIndexer(connection, provider, _profile(), batch_size=1).build(
        [_input([_chunk("a"), _chunk("b", content="beta", chunk_index=1)])]
    )
    repository = IndexRepository(connection)

    assert result.embedded_chunks == 2
    assert provider.document_calls == 2
    assert repository.counts() == {"sources": 1, "documents": 1, "chunks": 2, "vectors": 2}
    assert repository.active_profile_id() == _profile().profile_id
    assert repository.last_run()["status"] == "completed"
    assert connection.execute("SELECT visibility FROM chunks").fetchall()[0][0] == "public"
    assert connection.execute("SELECT metadata_json FROM documents").fetchone()[0]
    connection.close()


def test_second_identical_build_embeds_zero_chunks(database: Database) -> None:
    source_input = _input([_chunk("a"), _chunk("b", content="beta", chunk_index=1)])
    connection = database.connect()
    KnowledgeIndexer(connection, FakeEmbeddingProvider(), _profile()).build([source_input])
    second_provider = FakeEmbeddingProvider()

    result = KnowledgeIndexer(connection, second_provider, _profile()).build([source_input])

    assert result.plan.statistics()["unchanged"] == 2
    assert result.embedded_chunks == 0
    assert second_provider.document_calls == 0
    connection.close()


def test_vector_update_reuses_stable_integer_rowid(database: Database) -> None:
    connection = database.connect()
    indexer = KnowledgeIndexer(connection, FakeEmbeddingProvider(), _profile())
    indexer.build([_input([_chunk("a", content="alpha")])])
    row_id_before = connection.execute("SELECT id FROM chunks WHERE chunk_id='a'").fetchone()[0]

    changed = indexer.build([_input([_chunk("a", content="beta")])])
    row_id_after = connection.execute("SELECT id FROM chunks WHERE chunk_id='a'").fetchone()[0]
    vector_row_id = connection.execute("SELECT rowid FROM vec_chunks").fetchone()[0]

    assert len(changed.plan.changed) == 1
    assert row_id_before == row_id_after == vector_row_id
    connection.close()


def test_stale_cleanup_deletes_vector_metadata_and_state_but_is_source_scoped(
    database: Database,
) -> None:
    connection = database.connect()
    indexer = KnowledgeIndexer(connection, FakeEmbeddingProvider(), _profile())
    indexer.build([_input([_chunk("notes-a")])])
    indexer.build(
        [
            _input(
                [_chunk("identity-a", source_id="identity-profile")],
                source_id="identity-profile",
            )
        ]
    )

    result = indexer.build([_input([])])

    assert [item.chunk_id for item in result.plan.deleted] == ["notes-a"]
    assert connection.execute(
        "SELECT count(*) FROM chunks WHERE source_id='notes'"
    ).fetchone()[0] == 0
    assert connection.execute(
        "SELECT count(*) FROM chunks WHERE source_id='identity-profile'"
    ).fetchone()[0] == 1
    assert IndexRepository(connection).counts()["vectors"] == 1
    connection.close()


def test_persistent_cosine_search_joins_metadata_and_excludes_private(database: Database) -> None:
    connection = database.connect()
    provider = FakeEmbeddingProvider()
    indexer = KnowledgeIndexer(connection, provider, _profile())
    indexer.build(
        [
            _input([_chunk("public-alpha", content="alpha")]),
            _input(
                [
                    _chunk(
                        "private-alpha",
                        source_id="private-notes",
                        content="alpha",
                        visibility=Visibility.PRIVATE,
                    )
                ],
                source_id="private-notes",
                visibility=Visibility.PRIVATE,
            ),
        ]
    )
    indexer.build([_input([_chunk("public-beta", content="beta")], source_id="other")])

    results = search_public_vectors(connection, provider, _profile(), "alpha", top_k=5)

    assert [result.chunk_id for result in results] == ["public-alpha", "public-beta"]
    assert results[0].distance == pytest.approx(0.0)
    assert results[0].section_path == ["Section"]
    assert all(result.visibility == "public" for result in results)
    connection.close()


def test_active_profile_mismatch_requires_explicit_rebuild(database: Database) -> None:
    connection = database.connect()
    KnowledgeIndexer(connection, FakeEmbeddingProvider(), _profile()).build(
        [_input([_chunk("a")])]
    )
    changed_profile = _profile(normalize=False)

    with pytest.raises(IndexProfileMismatch, match="explicit rebuild"):
        plan_sources(IndexRepository(connection), [_input([_chunk("a")])], changed_profile)
    connection.close()


def test_profile_changing_rebuild_requires_all_indexed_sources(database: Database) -> None:
    connection = database.connect()
    indexer = KnowledgeIndexer(connection, FakeEmbeddingProvider(), _profile())
    indexer.build([_input([_chunk("a")])])
    indexer.build([_input([_chunk("b", source_id="other")], source_id="other")])

    with pytest.raises(RebuildScopeError, match="omitted: other"):
        plan_sources(
            IndexRepository(connection),
            [_input([_chunk("a")])],
            _profile(normalize=False),
            rebuild=True,
        )
    connection.close()


def test_explicit_full_rebuild_replaces_active_profile(database: Database) -> None:
    connection = database.connect()
    source_input = _input([_chunk("a")])
    KnowledgeIndexer(connection, FakeEmbeddingProvider(), _profile()).build([source_input])
    replacement = _profile(normalize=False)

    result = KnowledgeIndexer(
        connection, FakeEmbeddingProvider(), replacement
    ).build([source_input], rebuild=True)

    assert result.embedded_chunks == 1
    assert IndexRepository(connection).active_profile_id() == replacement.profile_id
    assert IndexRepository(connection).counts()["vectors"] == 1
    connection.close()


def test_incremental_lifecycle_reembeds_only_affected_chunks(database: Database) -> None:
    connection = database.connect()
    initial = [
        _chunk(f"chunk-{index}", content=f"section {index}", chunk_index=index)
        for index in range(10)
    ]
    KnowledgeIndexer(connection, FakeEmbeddingProvider(), _profile()).build([_input(initial)])

    modified = [
        chunk.model_copy(update={"content": "changed section"})
        if chunk.chunk_id == "chunk-4"
        else chunk
        for chunk in initial
    ]
    changed_provider = FakeEmbeddingProvider()
    changed = KnowledgeIndexer(connection, changed_provider, _profile()).build(
        [_input(modified)]
    )
    added = KnowledgeIndexer(connection, FakeEmbeddingProvider(), _profile()).build(
        [_input([*modified, _chunk("chunk-new", content="new section", chunk_index=10)])]
    )
    deleted = KnowledgeIndexer(connection, FakeEmbeddingProvider(), _profile()).build(
        [_input(modified[1:])]
    )

    assert len(changed.plan.changed) == changed.embedded_chunks == 1
    assert len(added.plan.new) == added.embedded_chunks == 1
    assert len(deleted.plan.deleted) == 2
    assert deleted.embedded_chunks == 0
    connection.close()


def test_invalid_embedding_shape_records_failed_run_without_applying(database: Database) -> None:
    connection = database.connect()
    source_input = _input([_chunk("a")])

    with pytest.raises(EmbeddingValidationError, match="shape"):
        KnowledgeIndexer(
            connection, FakeEmbeddingProvider(invalid_shape=True), _profile()
        ).build([source_input])

    repository = IndexRepository(connection)
    assert repository.counts() == {"sources": 0, "documents": 0, "chunks": 0, "vectors": 0}
    assert repository.last_run()["status"] == "failed"
    connection.close()


def test_third_embedding_batch_failure_keeps_previous_index_atomic(database: Database) -> None:
    connection = database.connect()
    initial_chunks = [
        _chunk(f"chunk-{index}", content="alpha", chunk_index=index) for index in range(5)
    ]
    KnowledgeIndexer(connection, FakeEmbeddingProvider(), _profile(), batch_size=2).build(
        [_input(initial_chunks)]
    )
    before = connection.execute(
        "SELECT chunk_id, content FROM chunks ORDER BY chunk_id"
    ).fetchall()

    changed_chunks = [chunk.model_copy(update={"content": "beta"}) for chunk in initial_chunks]
    failing_provider = FakeEmbeddingProvider(fail_on_call=3)
    with pytest.raises(RuntimeError, match="simulated embedding failure"):
        KnowledgeIndexer(connection, failing_provider, _profile(), batch_size=2).build(
            [_input(changed_chunks)]
        )

    after = connection.execute(
        "SELECT chunk_id, content FROM chunks ORDER BY chunk_id"
    ).fetchall()
    repository = IndexRepository(connection)
    assert [tuple(row) for row in after] == [tuple(row) for row in before]
    assert repository.counts()["vectors"] == 5
    assert repository.last_run()["status"] == "failed"
    assert search_public_vectors(
        connection, FakeEmbeddingProvider(), _profile(), "alpha", top_k=5
    )
    connection.close()


def test_database_reopen_preserves_index_and_search(database: Database) -> None:
    connection = database.connect()
    KnowledgeIndexer(connection, FakeEmbeddingProvider(), _profile()).build(
        [_input([_chunk("a")])]
    )
    connection.close()

    reopened = database.connect()
    results = search_public_vectors(
        reopened, FakeEmbeddingProvider(), _profile(), "alpha", top_k=1
    )

    assert IndexRepository(reopened).counts()["vectors"] == 1
    assert results[0].chunk_id == "a"
    reopened.close()
