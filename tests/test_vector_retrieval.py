from __future__ import annotations

import hashlib
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
from zglab_rag.evaluation.dataset import (
    EvaluationQuery,
    LoadedEvaluationDataset,
    QueryCategory,
    RelevantTarget,
    RetrievalEvaluationDataset,
)
from zglab_rag.evaluation.retrieval import compute_ranked_retrieval_metrics
from zglab_rag.evaluation.vector_retrieval import run_vector_retrieval_evaluation
from zglab_rag.indexing.errors import IndexProfileMismatch
from zglab_rag.indexing.indexer import KnowledgeIndexer
from zglab_rag.indexing.models import EmbeddingProfile, SourceIndexInput
from zglab_rag.retrieval.config import VectorRetrievalConfig
from zglab_rag.retrieval.contracts import RetrievalFilter, RetrievalQuery
from zglab_rag.retrieval.vector import VectorRetriever
from zglab_rag.storage.database import Database
from zglab_rag.storage.repositories import IndexRepository
from zglab_rag.storage.schema import VECTOR_DIMENSION


def _model_config() -> EmbeddingModelConfig:
    return EmbeddingModelConfig(
        id="bge-small-zh-v1.5",
        model_name="BAAI/bge-small-zh-v1.5",
        backend=EmbeddingBackend.SENTENCE_TRANSFORMERS,
        query_mode=QueryMode.BGE_ZH_INSTRUCTION,
        normalize=True,
        max_length=512,
    )


def _profile() -> EmbeddingProfile:
    return EmbeddingProfile.create(
        _model_config(),
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
        if query or "axis-0" in text:
            vector[0] = 1.0
            return vector
        if "tie" in text:
            vector[0] = 1.0
            vector[1] = 0.2
            return vector
        marker = next(
            (part for part in text.split() if part.startswith("order-")),
            "order-100",
        )
        vector[0] = 1.0
        vector[1] = int(marker.removeprefix("order-")) / 100.0
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
    document_content = "\n".join(content for _chunk_id, content in items)
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
        content=document_content,
        content_hash=hashlib.sha256(document_content.encode()).hexdigest(),
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
            content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            char_count=len(content),
            source_path=source_path,
            revision="revision-1",
        )
        for index, (chunk_id, content) in enumerate(items)
    ]
    return SourceIndexInput(
        source=source,
        revision="revision-1",
        documents=[document],
        chunks=chunks,
    )


@pytest.fixture
def retrieval_setup(tmp_path: Path):
    database = Database(tmp_path / "knowledge.db")
    connection = database.connect()
    provider = FakeEmbeddingProvider()
    inputs = [
        _source_input(
            "private",
            [(f"private-{index}", f"order-{index}") for index in range(1, 6)],
            visibility=Visibility.PRIVATE,
        ),
        _source_input("notes", [("public-a", "order-6"), ("public-b", "order-7")]),
        _source_input(
            "projects",
            [("project-a", "order-8")],
            scope=Scope.PROJECT,
        ),
    ]
    KnowledgeIndexer(connection, provider, _profile()).build(inputs)
    config = VectorRetrievalConfig(
        default_top_k=2,
        max_top_k=50,
        candidate_factor=2,
        minimum_candidate_k=2,
        maximum_candidate_k=50,
    )
    return database, connection, provider, config


def _retriever(setup) -> VectorRetriever:
    _database, connection, provider, config = setup
    return VectorRetriever(connection, provider, _profile(), config=config)


def test_basic_vector_retrieval_hydrates_metadata_score_and_distance(retrieval_setup) -> None:
    response = _retriever(retrieval_setup).retrieve(RetrievalQuery(text="query", top_k=1))
    result = response.results[0]

    assert result.chunk_id == "public-a"
    assert result.document_id == "notes:knowledge/notes.md"
    assert result.source_id == "notes"
    assert result.source_path == "knowledge/notes.md"
    assert result.section_path == ["Root", "public-a"]
    assert result.content == "order-6"
    assert result.revision == "revision-1"
    assert result.retriever == "vector"
    assert result.rank == 1
    assert result.score == pytest.approx(1.0 - result.distance)


def test_default_and_explicit_top_k_are_enforced(retrieval_setup) -> None:
    retriever = _retriever(retrieval_setup)

    assert len(retriever.retrieve(RetrievalQuery(text="query")).results) == 2
    assert len(retriever.retrieve(RetrievalQuery(text="query", top_k=1)).results) == 1


@pytest.mark.parametrize("top_k", [0, -1, 51])
def test_invalid_top_k_is_rejected(retrieval_setup, top_k: int) -> None:
    with pytest.raises(ValueError, match="top_k"):
        _retriever(retrieval_setup).retrieve(RetrievalQuery(text="query", top_k=top_k))


def test_profile_validation_rejects_provider_mismatch(retrieval_setup) -> None:
    _database, connection, provider, config = retrieval_setup
    provider.model_name = "other/model"

    with pytest.raises(IndexProfileMismatch, match="provider model mismatch"):
        VectorRetriever(connection, provider, _profile(), config=config)


def test_profile_validation_rejects_active_index_mismatch(retrieval_setup) -> None:
    _database, connection, provider, config = retrieval_setup
    connection.execute(
        "UPDATE index_metadata SET value='different' "
        "WHERE key='active_embedding_profile_id'"
    )

    with pytest.raises(IndexProfileMismatch, match="active index"):
        VectorRetriever(connection, provider, _profile(), config=config)


def test_private_top_candidates_never_leak_and_overfetch_fills_top_k(retrieval_setup) -> None:
    response = _retriever(retrieval_setup).retrieve(RetrievalQuery(text="query", top_k=2))

    assert [result.chunk_id for result in response.results] == ["public-a", "public-b"]
    assert all(result.visibility == Visibility.PUBLIC for result in response.results)
    assert response.diagnostics.candidate_count == 8
    assert response.diagnostics.filtered_count == 5


def test_private_filter_cannot_be_requested() -> None:
    with pytest.raises(ValueError, match="public-only"):
        RetrievalFilter(visibility=Visibility.PRIVATE)


def test_source_filter_skips_disallowed_higher_ranked_chunks(retrieval_setup) -> None:
    response = _retriever(retrieval_setup).retrieve(
        RetrievalQuery(
            text="query",
            top_k=1,
            filters=RetrievalFilter(source_ids=("projects",)),
        )
    )

    assert [result.source_id for result in response.results] == ["projects"]


def test_scope_and_combined_filters(retrieval_setup) -> None:
    retriever = _retriever(retrieval_setup)
    by_scope = retriever.retrieve(
        RetrievalQuery(
            text="query",
            top_k=1,
            filters=RetrievalFilter(scopes=(Scope.PROJECT,)),
        )
    )
    combined_empty = retriever.retrieve(
        RetrievalQuery(
            text="query",
            top_k=2,
            filters=RetrievalFilter(source_ids=("notes",), scopes=(Scope.PROJECT,)),
        )
    )

    assert [result.scope for result in by_scope.results] == [Scope.PROJECT]
    assert combined_empty.results == []


def test_equal_distances_use_deterministic_chunk_id_order(tmp_path: Path) -> None:
    connection = Database(tmp_path / "ties.db").connect()
    provider = FakeEmbeddingProvider()
    KnowledgeIndexer(connection, provider, _profile()).build(
        [_source_input("notes", [("z-tie", "tie"), ("a-tie", "tie")])]
    )
    retriever = VectorRetriever(connection, provider, _profile())

    first = retriever.retrieve(RetrievalQuery(text="query", top_k=2)).results
    second = retriever.retrieve(RetrievalQuery(text="query", top_k=2)).results

    assert [item.chunk_id for item in first] == ["a-tie", "z-tie"]
    assert [item.chunk_id for item in second] == ["a-tie", "z-tie"]
    connection.close()


def test_database_reopen_retrieval(retrieval_setup) -> None:
    database, connection, provider, config = retrieval_setup
    connection.close()
    reopened = database.connect(read_only=True, initialize=False)

    results = VectorRetriever(reopened, provider, _profile(), config=config).retrieve(
        RetrievalQuery(text="query", top_k=1)
    ).results

    assert results[0].chunk_id == "public-a"
    reopened.close()


def test_search_does_not_mutate_database(retrieval_setup) -> None:
    _database, connection, _provider, _config = retrieval_setup
    before_changes = connection.total_changes
    before_run = IndexRepository(connection).last_run()["run_id"]

    _retriever(retrieval_setup).retrieve(RetrievalQuery(text="query"))

    assert connection.total_changes == before_changes
    assert IndexRepository(connection).last_run()["run_id"] == before_run


def test_hit_rate_recall_and_mrr_keep_distinct_semantics() -> None:
    results = _retriever_result_items()
    queries = [
        EvaluationQuery(
            id="q1",
            query="query",
            category=QueryCategory.KNOWLEDGE,
            relevant=[
                RelevantTarget(
                    source_id="notes",
                    source_path="knowledge/notes.md",
                    section_path=["Root", "public-a"],
                ),
                RelevantTarget(
                    source_id="notes",
                    source_path="knowledge/notes.md",
                    section_path=["Root", "missing"],
                ),
            ],
        )
    ]

    metrics = compute_ranked_retrieval_metrics(queries, [results], cutoffs=(1, 2))

    assert metrics.recall_at == {1: 0.5, 2: 0.5}
    assert metrics.hit_rate_at == {1: 1.0, 2: 1.0}
    assert metrics.mrr == 1.0


def _retriever_result_items():
    from zglab_rag.retrieval.contracts import RetrievalResult

    return [
        RetrievalResult(
            chunk_id="public-a",
            document_id="notes:knowledge/notes.md",
            source_id="notes",
            source_path="knowledge/notes.md",
            scope=Scope.KNOWLEDGE,
            title="Title",
            section_path=["Root", "public-a"],
            content="content",
            visibility=Visibility.PUBLIC,
            revision="revision-1",
            rank=1,
            score=1.0,
            distance=0.0,
        )
    ]


def test_hard_negative_diagnostics_and_latency_are_recorded(retrieval_setup) -> None:
    retriever = _retriever(retrieval_setup)
    positive = EvaluationQuery(
        id="positive",
        query="query",
        category=QueryCategory.KNOWLEDGE,
        relevant=[
            RelevantTarget(
                source_id="notes",
                source_path="knowledge/notes.md",
                section_path=["Root", "public-a"],
            )
        ],
    )
    hard = EvaluationQuery(
        id="hard",
        query="unknown",
        category=QueryCategory.HARD_NEGATIVE,
    )
    dataset = LoadedEvaluationDataset(
        dataset=RetrievalEvaluationDataset(version=1, queries=[positive, hard]),
        sha256="a" * 64,
        path=Path("evaluation/retrieval.yaml"),
    )
    evidence = {("notes", "knowledge/notes.md", ("Root", "public-a"))}

    result = run_vector_retrieval_evaluation(
        retriever=retriever,
        dataset=dataset,
        evidence=evidence,
        source_ids=["notes"],
        embedding_profile_id=_profile().profile_id,
    )

    assert result.overall.hit_rate_at[1] == 1.0
    assert result.overall.recall_at[1] == 1.0
    assert result.overall.mrr == 1.0
    assert result.hard_negatives[0].top1_score is not None
    assert result.hard_negatives[0].top1_top2_margin is not None
    assert result.positive_top1_score.minimum <= result.positive_top1_score.maximum
    assert result.total_retrieval_latency.p95_ms >= 0
