from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from zglab_rag.domain.lexical import DEFAULT_LEXICAL_PROFILE, LexicalProfile
from zglab_rag.domain.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    Scope,
    SourceDefinition,
    SourceKind,
    Visibility,
)
from zglab_rag.embeddings.config import EmbeddingBackend, EmbeddingModelConfig, QueryMode
from zglab_rag.evaluation.composition import TextComposition
from zglab_rag.evaluation.dataset import (
    EvaluationQuery,
    LoadedEvaluationDataset,
    QueryCategory,
    RelevantTarget,
    RetrievalEvaluationDataset,
)
from zglab_rag.evaluation.retrieval_compare import run_retrieval_comparison
from zglab_rag.indexing.indexer import KnowledgeIndexer
from zglab_rag.indexing.models import EmbeddingProfile, SourceIndexInput
from zglab_rag.retrieval.config import HybridRetrievalConfig, VectorRetrievalConfig
from zglab_rag.retrieval.contracts import RetrievalFilter, RetrievalQuery, RetrievalResult
from zglab_rag.retrieval.errors import LexicalProfileMismatch
from zglab_rag.retrieval.hybrid import HybridRetriever, reciprocal_rank_fusion
from zglab_rag.retrieval.lexical import LexicalRetriever
from zglab_rag.retrieval.query import prepare_lexical_query
from zglab_rag.retrieval.vector import VectorRetriever
from zglab_rag.storage.capabilities import probe_fts5
from zglab_rag.storage.database import Database
from zglab_rag.storage.repositories import IndexRepository
from zglab_rag.storage.schema import VECTOR_DIMENSION


def _profile():
    config = EmbeddingModelConfig(
        id="bge-small-zh-v1.5",
        model_name="BAAI/bge-small-zh-v1.5",
        backend=EmbeddingBackend.SENTENCE_TRANSFORMERS,
        query_mode=QueryMode.BGE_ZH_INSTRUCTION,
        normalize=True,
        max_length=512,
    )
    return EmbeddingProfile.create(
        config,
        dimension=VECTOR_DIMENSION,
        composition=TextComposition.CONTEXTUAL,
    )


class FakeEmbeddingProvider:
    model_name = "BAAI/bge-small-zh-v1.5"
    dimension = VECTOR_DIMENSION
    device = "cpu"

    def encode_documents(self, texts):
        return np.stack([self._vector(text) for text in texts])

    def encode_queries(self, texts):
        return np.stack([self._vector(text, query=True) for text in texts])

    @staticmethod
    def _vector(text: str, *, query: bool = False) -> np.ndarray:
        vector = np.zeros(VECTOR_DIMENSION, dtype=np.float32)
        vector[0] = 1.0
        digest = hashlib.sha256(text.encode()).digest()
        if not query:
            vector[1] = digest[0] / 2550
        return vector


def _source_input(
    source_id: str,
    items: list[tuple[str, str]],
    *,
    scope: Scope = Scope.KNOWLEDGE,
    visibility: Visibility = Visibility.PUBLIC,
) -> SourceIndexInput:
    source_path = f"knowledge/{source_id}.md"
    document_id = f"{source_id}:{source_path}"
    content = "\n".join(item_content for _chunk_id, item_content in items)
    source = SourceDefinition(
        id=source_id,
        kind=SourceKind.LOCAL,
        scope=scope,
        visibility=visibility,
        priority=80,
        path=source_path,
        include=[source_path],
    )
    document = KnowledgeDocument(
        document_id=document_id,
        source_id=source_id,
        source_kind=SourceKind.LOCAL,
        scope=scope,
        visibility=visibility,
        priority=80,
        path=source_path,
        title=f"Title {source_id}",
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
    )
    chunks = [
        KnowledgeChunk(
            chunk_id=chunk_id,
            document_id=document_id,
            source_id=source_id,
            scope=scope,
            visibility=visibility,
            priority=80,
            title=f"Title {source_id}",
            section_path=["Root", chunk_id],
            chunk_index=index,
            content=item_content,
            content_hash=hashlib.sha256(item_content.encode()).hexdigest(),
            char_count=len(item_content),
            source_path=source_path,
            revision="revision-1",
        )
        for index, (chunk_id, item_content) in enumerate(items)
    ]
    return SourceIndexInput(
        source=source,
        revision="revision-1",
        documents=[document],
        chunks=chunks,
    )


@pytest.fixture
def lexical_setup(tmp_path: Path):
    database = Database(tmp_path / "knowledge.db")
    connection = database.connect()
    provider = FakeEmbeddingProvider()
    inputs = [
        _source_input(
            "notes",
            [
                ("memory", "Agent 长期记忆通过分层 Memory 和 Context 管理"),
                ("websocket", "WebSocket normal close generation fencing"),
            ],
        ),
        _source_input(
            "projects",
            [("spring", "Spring constructor startup failure sqlite-vec")],
            scope=Scope.PROJECT,
        ),
        _source_input(
            "private",
            [("private-secret", "Agent 长期记忆 private secret")],
            visibility=Visibility.PRIVATE,
        ),
    ]
    KnowledgeIndexer(connection, provider, _profile()).build(inputs)
    config = VectorRetrievalConfig(
        default_top_k=3,
        max_top_k=50,
        candidate_factor=2,
        minimum_candidate_k=2,
        maximum_candidate_k=50,
    )
    return database, connection, provider, inputs, config


def _lexical(setup) -> LexicalRetriever:
    return LexicalRetriever(setup[1], config=setup[4])


def _hybrid(setup, **updates) -> HybridRetriever:
    config = HybridRetrievalConfig(
        default_top_k=3,
        max_top_k=50,
        vector_candidate_k=5,
        lexical_candidate_k=5,
        **updates,
    )
    return HybridRetriever(
        VectorRetriever(setup[1], setup[2], _profile(), config=setup[4]),
        _lexical(setup),
        config=config,
    )


def test_fts5_capability_probe_checks_trigram_and_bm25() -> None:
    capabilities = probe_fts5(sqlite3.connect(":memory:"))

    assert capabilities.enabled is True
    assert capabilities.trigram is True
    assert capabilities.bm25 is True


def test_v1_to_v2_migration_preserves_vectors_and_backfills_fts(tmp_path: Path) -> None:
    database = Database(tmp_path / "migration.db")
    connection = database.connect()
    KnowledgeIndexer(connection, FakeEmbeddingProvider(), _profile()).build(
        [_source_input("notes", [("memory", "Agent 长期记忆")])]
    )
    vector_before = IndexRepository(connection).vector_count()
    connection.execute("DROP TABLE fts_chunks")
    connection.execute("DROP TABLE lexical_profiles")
    connection.execute("DELETE FROM index_metadata WHERE key='active_lexical_profile_id'")
    connection.execute("UPDATE schema_metadata SET value='1' WHERE key='schema_version'")
    connection.close()

    migrated = database.connect(initialize=False, migrate=True)
    repository = IndexRepository(migrated)

    assert Database.versions(migrated).schema == 3
    assert repository.vector_count() == vector_before == 1
    assert repository.lexical_count() == 1
    assert _lexical((database, migrated, None, None, VectorRetrievalConfig())).retrieve(
        RetrievalQuery(text="长期记忆", top_k=1)
    ).results[0].chunk_id == "memory"
    migrated.close()


def test_lexical_insert_chinese_and_english_queries(lexical_setup) -> None:
    retriever = _lexical(lexical_setup)

    chinese = retriever.retrieve(RetrievalQuery(text="长期记忆", top_k=1))
    assert chinese.results[0].chunk_id == "memory"
    assert (
        retriever.retrieve(RetrievalQuery(text="WebSocket normal close", top_k=1))
        .results[0]
        .chunk_id
        == "websocket"
    )


def test_lexical_update_and_delete_follow_atomic_index_lifecycle(lexical_setup) -> None:
    _database, connection, provider, inputs, _config = lexical_setup
    original = inputs[0]
    old_chunk = original.chunks[0]
    new_content = "CAS unified authentication"
    changed_chunk = old_chunk.model_copy(
        update={
            "content": new_content,
            "content_hash": hashlib.sha256(new_content.encode()).hexdigest(),
            "char_count": len(new_content),
        }
    )
    changed_document = original.documents[0].model_copy(
        update={
            "content": new_content,
            "content_hash": hashlib.sha256(new_content.encode()).hexdigest(),
        }
    )
    changed = replace(original, documents=[changed_document], chunks=[changed_chunk])
    KnowledgeIndexer(connection, provider, _profile()).build([changed])

    retriever = LexicalRetriever(connection)
    updated = retriever.retrieve(RetrievalQuery(text="unified authentication"))
    assert updated.results[0].chunk_id == "memory"
    assert retriever.retrieve(RetrievalQuery(text="长期记忆")).results == []

    KnowledgeIndexer(connection, provider, _profile()).build(
        [replace(original, documents=[], chunks=[])]
    )
    assert retriever.retrieve(RetrievalQuery(text="unified authentication")).results == []


def test_lexical_reopen_is_read_only(lexical_setup) -> None:
    database, connection, *_rest = lexical_setup
    connection.close()
    reopened = database.connect(read_only=True, initialize=False)
    before = reopened.total_changes

    results = LexicalRetriever(reopened).retrieve(RetrievalQuery(text="sqlite-vec")).results

    assert results[0].chunk_id == "spring"
    assert reopened.total_changes == before
    reopened.close()


@pytest.mark.parametrize(
    "query",
    [
        'RAG / Agent (Memory) "Context"',
        "Spring-Boot",
        "LLM Provider",
        "WebSocket / normal-close",
    ],
)
def test_query_preparation_escapes_special_syntax(query: str) -> None:
    prepared = prepare_lexical_query(query)

    assert prepared.applicable is True
    assert prepared.match_expression is not None


@pytest.mark.parametrize("query", ["AI", "中", "  "])
def test_short_query_is_gracefully_not_applicable(lexical_setup, query: str) -> None:
    response = _lexical(lexical_setup).retrieve(RetrievalQuery(text=query))

    assert response.results == []
    assert response.diagnostics.lexical_applicable is False
    assert response.diagnostics.lexical_not_applicable_reason


def test_bm25_ordering_is_deterministic_and_raw_score_is_lower_better(lexical_setup) -> None:
    retriever = _lexical(lexical_setup)
    first = retriever.retrieve(RetrievalQuery(text="Agent 长期记忆", top_k=3)).results
    second = retriever.retrieve(RetrievalQuery(text="Agent 长期记忆", top_k=3)).results

    assert [item.chunk_id for item in first] == [item.chunk_id for item in second]
    assert all(item.score == -item.raw_bm25 for item in first)
    assert [item.raw_bm25 for item in first] == sorted(item.raw_bm25 for item in first)


def test_column_weights_are_configurable_and_profile_is_deterministic() -> None:
    equal = LexicalProfile.create(
        tokenizer="trigram",
        title_weight=1,
        section_weight=1,
        content_weight=1,
        config_version=1,
    )
    weighted = LexicalProfile.create(
        tokenizer="trigram",
        title_weight=2,
        section_weight=2,
        content_weight=1,
        config_version=1,
    )

    assert equal.profile_id == LexicalProfile.create(
        tokenizer="trigram",
        title_weight=1,
        section_weight=1,
        content_weight=1,
        config_version=1,
    ).profile_id
    assert equal.profile_id != weighted.profile_id
    assert equal.profile_id == DEFAULT_LEXICAL_PROFILE.profile_id
    assert weighted.profile_id != DEFAULT_LEXICAL_PROFILE.profile_id


def test_bm25_column_weights_change_title_contribution(tmp_path: Path) -> None:
    connection = Database(tmp_path / "weights.db").connect()
    title_source = _source_input("title", [("title-hit", "ordinary filler text")])
    title_source.documents[0].title = "generation fencing"
    title_source.chunks[0].title = "generation fencing"
    content_source = _source_input(
        "content",
        [("content-hit", "generation fencing")],
    )
    KnowledgeIndexer(connection, FakeEmbeddingProvider(), _profile()).build(
        [title_source, content_source]
    )
    repository = IndexRepository(connection)
    expression = prepare_lexical_query("generation fencing").match_expression
    equal = repository.lexical_search(
        expression or "",
        top_k=2,
        visibility="public",
        title_weight=1,
        section_weight=1,
        content_weight=1,
    )
    weighted = repository.lexical_search(
        expression or "",
        top_k=2,
        visibility="public",
        title_weight=2,
        section_weight=1,
        content_weight=1,
    )
    equal_scores = {row["chunk_id"]: row["raw_bm25"] for row in equal}
    weighted_scores = {row["chunk_id"]: row["raw_bm25"] for row in weighted}

    assert weighted_scores["title-hit"] < equal_scores["title-hit"]
    assert weighted_scores["content-hit"] == pytest.approx(equal_scores["content-hit"])
    connection.close()


def test_lexical_profile_mismatch_is_rejected(lexical_setup) -> None:
    mismatched = LexicalProfile.create(
        tokenizer="trigram",
        title_weight=2,
        section_weight=2,
        content_weight=1,
        config_version=1,
    )
    with pytest.raises(LexicalProfileMismatch):
        LexicalRetriever(lexical_setup[1], profile=mismatched)


def test_lexical_public_source_scope_and_combined_filters(lexical_setup) -> None:
    retriever = _lexical(lexical_setup)
    public = retriever.retrieve(RetrievalQuery(text="Agent 长期记忆", top_k=5)).results
    source = retriever.retrieve(
        RetrievalQuery(
            text="sqlite-vec",
            filters=RetrievalFilter(source_ids=("projects",)),
        )
    ).results
    scope = retriever.retrieve(
        RetrievalQuery(
            text="sqlite-vec",
            filters=RetrievalFilter(scopes=(Scope.PROJECT,)),
        )
    ).results
    combined = retriever.retrieve(
        RetrievalQuery(
            text="sqlite-vec",
            filters=RetrievalFilter(source_ids=("notes",), scopes=(Scope.PROJECT,)),
        )
    ).results

    assert all(item.visibility == Visibility.PUBLIC for item in public)
    assert "private-secret" not in {item.chunk_id for item in public}
    assert [item.source_id for item in source] == ["projects"]
    assert [item.scope for item in scope] == [Scope.PROJECT]
    assert combined == []


def _result(chunk_id: str, rank: int, *, retriever: str) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        document_id="document",
        source_id="source",
        source_path="knowledge/source.md",
        scope=Scope.KNOWLEDGE,
        title="Title",
        section_path=["Section"],
        content="content",
        visibility=Visibility.PUBLIC,
        revision=None,
        rank=rank,
        score=1.0,
        retriever=retriever,
    )


def test_rrf_calculation_missing_side_and_deterministic_tie() -> None:
    vector = [_result("shared", 1, retriever="vector"), _result("vector", 2, retriever="vector")]
    lexical = [
        _result("shared", 1, retriever="lexical"),
        _result("lexical", 2, retriever="lexical"),
    ]

    fused = reciprocal_rank_fusion(vector, lexical, rrf_k=60)

    assert fused[0].chunk_id == "shared"
    assert fused[0].rrf_score == pytest.approx(2 / 61)
    assert [item.chunk_id for item in fused[1:]] == ["lexical", "vector"]
    assert fused[1].rrf_score == pytest.approx(1 / 62)


def test_hybrid_top_k_uses_both_pools_and_remains_public(lexical_setup) -> None:
    response = _hybrid(lexical_setup).retrieve(
        RetrievalQuery(text="Agent 长期记忆", top_k=2)
    )

    assert len(response.results) == 2
    assert response.diagnostics.vector_candidate_count > 2
    assert response.diagnostics.lexical_candidate_count > 0
    assert any(item.vector_rank is not None for item in response.results)
    assert any(item.lexical_rank is not None for item in response.results)
    assert all(item.visibility == Visibility.PUBLIC for item in response.results)


def test_hybrid_short_query_falls_back_to_vector(lexical_setup) -> None:
    response = _hybrid(lexical_setup).retrieve(RetrievalQuery(text="AI", top_k=2))

    assert len(response.results) == 2
    assert response.diagnostics.lexical_applicable is False
    assert all(item.lexical_rank is None for item in response.results)


def test_evaluation_comparison_reports_all_modes_and_delta(lexical_setup) -> None:
    _database, connection, *_rest = lexical_setup
    query = EvaluationQuery(
        id="memory",
        query="Agent 长期记忆",
        category=QueryCategory.KNOWLEDGE,
        relevant=[
            RelevantTarget(
                source_id="notes",
                source_path="knowledge/notes.md",
                section_path=["Root", "memory"],
            )
        ],
    )
    hard = EvaluationQuery(
        id="hard",
        query="unknown impossible phrase",
        category=QueryCategory.HARD_NEGATIVE,
    )
    dataset = LoadedEvaluationDataset(
        dataset=RetrievalEvaluationDataset(version=1, queries=[query, hard]),
        sha256="a" * 64,
        path=Path("evaluation/retrieval.yaml"),
    )
    vector = VectorRetriever(connection, lexical_setup[2], _profile(), config=lexical_setup[4])
    lexical = _lexical(lexical_setup)
    hybrid = _hybrid(lexical_setup)
    result = run_retrieval_comparison(
        {"vector": vector, "lexical": lexical, "hybrid": hybrid},
        dataset=dataset,
        evidence={("notes", "knowledge/notes.md", ("Root", "memory"))},
        source_ids=["notes"],
        embedding_profile_id=_profile().profile_id,
        lexical_profile_id=DEFAULT_LEXICAL_PROFILE.profile_id,
    )

    assert set(result.modes) == {"vector", "lexical", "hybrid"}
    assert result.modes["lexical"].overall.recall_at[1] == 1.0
    assert "recall_at_20" in result.hybrid_minus_vector
    assert result.modes["hybrid"].latency.max_ms >= 0
