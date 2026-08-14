from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tests.test_vector_retrieval import FakeEmbeddingProvider, _profile, _source_input
from zglab_rag.domain.models import Scope, Visibility
from zglab_rag.evaluation.dataset import (
    EvaluationQuery,
    LoadedEvaluationDataset,
    QueryCategory,
    RelevantTarget,
    RetrievalEvaluationDataset,
)
from zglab_rag.evaluation.reranker_compare import evaluate_candidate_k
from zglab_rag.evaluation.retrieval import compute_ranked_retrieval_metrics
from zglab_rag.indexing.indexer import KnowledgeIndexer
from zglab_rag.reranking.config import (
    RerankerBackend,
    RerankerConfigurationError,
    RerankerModelConfig,
    RerankerModelRegistry,
)
from zglab_rag.reranking.cross_encoder import CrossEncoderRerankerProvider
from zglab_rag.reranking.passage import compose_passage_context
from zglab_rag.reranking.service import RerankedRetriever, RerankerRetrievalConfig
from zglab_rag.retrieval.config import VectorRetrievalConfig
from zglab_rag.retrieval.contracts import (
    RetrievalDiagnostics,
    RetrievalFilter,
    RetrievalQuery,
    RetrievalResponse,
    RetrievalResult,
)
from zglab_rag.retrieval.vector import VectorRetriever
from zglab_rag.storage.database import Database


def _result(
    chunk_id: str,
    rank: int,
    *,
    source_id: str = "notes",
    scope: Scope = Scope.KNOWLEDGE,
) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        document_id=f"{source_id}:document",
        source_id=source_id,
        source_path=f"knowledge/{source_id}.md",
        scope=scope,
        title=f"Title {chunk_id}",
        section_path=["Root", chunk_id],
        content=f"content {chunk_id}",
        visibility=Visibility.PUBLIC,
        revision="revision-1",
        rank=rank,
        score=1.0 - rank / 100,
        distance=rank / 100,
    )


class StubVectorRetriever:
    def __init__(self, results: list[RetrievalResult]) -> None:
        self.results = results
        self.queries: list[RetrievalQuery] = []

    @property
    def corpus_size(self) -> int:
        return len(self.results)

    def retrieve(self, query: RetrievalQuery) -> RetrievalResponse:
        self.queries.append(query)
        selected = [
            item
            for item in self.results
            if (not query.filters.source_ids or item.source_id in query.filters.source_ids)
            and (not query.filters.scopes or item.scope in query.filters.scopes)
        ][: query.top_k]
        return RetrievalResponse(
            results=selected,
            diagnostics=RetrievalDiagnostics(
                query_embedding_latency_ms=0.1,
                vector_search_latency_ms=0.1,
                total_retrieval_latency_ms=0.2,
                candidate_count=len(selected),
                filtered_count=0,
                returned_count=len(selected),
                top_k=query.top_k or 5,
                filters=query.filters,
            ),
        )


class FakeRerankerProvider:
    model_name = "fake/reranker"
    backend = "torch"
    device = "cpu"
    batch_size = 4

    def __init__(self, scores: list[float] | Exception) -> None:
        self.scores = scores
        self.calls: list[tuple[str, list[str]]] = []

    def score(self, query: str, passages) -> np.ndarray:
        self.calls.append((query, list(passages)))
        if isinstance(self.scores, Exception):
            raise self.scores
        return np.asarray(self.scores[: len(passages)], dtype=np.float32)


def _pipeline(
    scores: list[float] | Exception,
    *,
    results: list[RetrievalResult] | None = None,
    candidate_k: int = 10,
) -> tuple[RerankedRetriever, StubVectorRetriever, FakeRerankerProvider]:
    default_results = [_result(f"chunk-{rank}", rank) for rank in range(1, 11)]
    vector = StubVectorRetriever(results or default_results)
    provider = FakeRerankerProvider(scores)
    pipeline = RerankedRetriever(
        vector,
        provider,
        config=RerankerRetrievalConfig(
            default_top_k=5,
            maximum_top_k=candidate_k,
            candidate_k=candidate_k,
        ),
    )
    return pipeline, vector, provider


def test_reranker_config_registry_parsing_and_disabled_reference() -> None:
    registry = RerankerModelRegistry.from_yaml("config/reranker-models.yaml")

    assert registry.get_enabled("mmarco-mMiniLMv2-L12-H384-v1").backend == RerankerBackend.TORCH
    assert registry.get_enabled("mmarco-mMiniLMv2-L12-H384-v1").batch_size == 16
    with pytest.raises(RerankerConfigurationError, match="disabled"):
        registry.get_enabled("bge-reranker-base")


def test_duplicate_reranker_config_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "rerankers.yaml"
    path.write_text(
        """version: 1
models:
  - {id: duplicate, model_name: a, backend: torch, max_length: 10, batch_size: 1}
  - {id: duplicate, model_name: b, backend: torch, max_length: 10, batch_size: 1}
""",
        encoding="utf-8",
    )
    with pytest.raises(RerankerConfigurationError, match="duplicate"):
        RerankerModelRegistry.from_yaml(path)


@pytest.mark.parametrize("candidate_k", [0, 9, 11, 31])
def test_candidate_k_validation(candidate_k: int) -> None:
    with pytest.raises(ValueError, match="candidate_k"):
        RerankerRetrievalConfig(candidate_k=candidate_k, maximum_top_k=5)


@pytest.mark.parametrize("candidate_k", [10, 20, 30])
def test_supported_candidate_k(candidate_k: int) -> None:
    config = RerankerRetrievalConfig(
        candidate_k=candidate_k,
        maximum_top_k=candidate_k,
    )
    assert config.candidate_k == candidate_k


def test_top_k_cannot_exceed_candidate_pool() -> None:
    pipeline, _vector, _provider = _pipeline([1.0] * 10)
    with pytest.raises(ValueError, match="candidate_k"):
        pipeline.retrieve(RetrievalQuery(text="query", top_k=11))


def test_cross_encoder_provider_constructs_official_query_passage_pairs() -> None:
    class FakeModel:
        inputs = None
        kwargs = None

        def predict(self, inputs, **kwargs):
            self.inputs = inputs
            self.kwargs = kwargs
            return [0.25, 0.75]

    model = FakeModel()
    config = RerankerModelConfig(
        id="fake",
        model_name="fake/model",
        backend=RerankerBackend.TORCH,
        max_length=128,
        batch_size=7,
    )
    provider = CrossEncoderRerankerProvider(
        config,
        model_factory=lambda _config, _device: model,
    )

    scores = provider.score("question", ["first", "second"])

    assert model.inputs == [("question", "first"), ("question", "second")]
    assert model.kwargs["batch_size"] == 7
    assert scores.tolist() == pytest.approx([0.25, 0.75])


def test_passage_context_has_one_stable_title_section_content_format() -> None:
    passage = compose_passage_context(_result("evidence", 1))

    assert passage == (
        "Title: Title evidence\n"
        "Section: Root > evidence\n\n"
        "content evidence"
    )


def test_descending_score_preserves_original_rank_vector_score_and_ties() -> None:
    pipeline, _vector, _provider = _pipeline([0.4, 0.9, 0.9, 0.1, 0, 0, 0, 0, 0, 0])

    results = pipeline.retrieve(RetrievalQuery(text="query", top_k=3)).results

    assert [item.chunk_id for item in results] == ["chunk-2", "chunk-3", "chunk-1"]
    assert results[0].original_rank == 2
    assert results[0].rerank_rank == results[0].rank == 1
    assert results[0].vector_score == pytest.approx(0.98)
    assert results[0].reranker_score == results[0].score == pytest.approx(0.9)
    assert results[0].retriever == "reranked"


def test_relevant_can_be_promoted_or_demoted() -> None:
    promoted, *_ = _pipeline([0.0, 0.0, 1.0, 0, 0, 0, 0, 0, 0, 0])
    demoted, *_ = _pipeline([0.0, 1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2])

    promoted_results = promoted.retrieve(RetrievalQuery(text="query", top_k=10)).results
    demoted_results = demoted.retrieve(RetrievalQuery(text="query", top_k=10)).results

    assert next(item.rank for item in promoted_results if item.chunk_id == "chunk-3") == 1
    assert next(item.rank for item in demoted_results if item.chunk_id == "chunk-1") == 10


def test_candidate_outside_top_n_cannot_appear() -> None:
    candidates = [_result(f"chunk-{rank}", rank) for rank in range(1, 31)]
    pipeline, _vector, provider = _pipeline([0.0] * 10, results=candidates)

    results = pipeline.retrieve(RetrievalQuery(text="query", top_k=10)).results

    assert {item.chunk_id for item in results} == {f"chunk-{rank}" for rank in range(1, 11)}
    assert len(provider.calls[0][1]) == 10


def test_recall_at_candidate_k_is_invariant() -> None:
    pipeline, vector, _provider = _pipeline(list(reversed(range(10))))
    query = EvaluationQuery(
        id="relevant",
        query="query",
        category=QueryCategory.KNOWLEDGE,
        relevant=[
            RelevantTarget(
                source_id="notes",
                source_path="knowledge/notes.md",
                section_path=["Root", "chunk-8"],
            )
        ],
    )
    reranked = pipeline.retrieve(RetrievalQuery(text="query", top_k=10)).results
    original = vector.results[:10]

    vector_metrics = compute_ranked_retrieval_metrics([query], [original], cutoffs=(10,))
    reranked_metrics = compute_ranked_retrieval_metrics([query], [reranked], cutoffs=(10,))
    assert vector_metrics.recall_at[10] == reranked_metrics.recall_at[10] == 1.0


def test_public_source_and_scope_filters_are_inherited() -> None:
    results = [
        _result("notes", 1),
        _result("project", 2, source_id="projects", scope=Scope.PROJECT),
    ]
    pipeline, vector, _provider = _pipeline([0.5], results=results)
    response = pipeline.retrieve(
        RetrievalQuery(
            text="query",
            top_k=1,
            filters=RetrievalFilter(source_ids=("projects",), scopes=(Scope.PROJECT,)),
        )
    )

    assert [item.chunk_id for item in response.results] == ["project"]
    assert vector.queries[0].filters.visibility == Visibility.PUBLIC


def test_empty_candidates_do_not_call_provider() -> None:
    pipeline, _vector, provider = _pipeline([1.0], results=[])
    pipeline.vector_retriever.results = []

    response = pipeline.retrieve(RetrievalQuery(text="query"))

    assert response.results == []
    assert provider.calls == []
    assert response.diagnostics.pairs_scored == 0


def test_provider_failure_and_invalid_shape_are_explicit() -> None:
    failing, *_ = _pipeline(RuntimeError("provider failed"))
    invalid, *_ = _pipeline([1.0])

    with pytest.raises(RuntimeError, match="provider failed"):
        failing.retrieve(RetrievalQuery(text="query"))
    with pytest.raises(ValueError, match="invalid scores"):
        invalid.retrieve(RetrievalQuery(text="query"))


def test_evaluation_metrics_delta_promotions_and_hard_negatives() -> None:
    results = [_result(f"chunk-{rank}", rank) for rank in range(1, 11)]
    pipeline, _vector, _provider = _pipeline(
        [0.1, 0.2, 0.9, 0.0, -0.1, -0.2, -0.3, -0.4, -0.5, -0.6],
        results=results,
    )
    positive = EvaluationQuery(
        id="positive",
        query="query",
        category=QueryCategory.KNOWLEDGE,
        relevant=[
            RelevantTarget(
                source_id="notes",
                source_path="knowledge/notes.md",
                section_path=["Root", "chunk-3"],
            )
        ],
    )
    hard = EvaluationQuery(
        id="hard",
        query="hard query",
        category=QueryCategory.HARD_NEGATIVE,
    )
    dataset = LoadedEvaluationDataset(
        dataset=RetrievalEvaluationDataset(version=1, queries=[positive, hard]),
        sha256="a" * 64,
        path=Path("evaluation/retrieval.yaml"),
    )
    result = evaluate_candidate_k(
        pipeline,
        dataset=dataset,
        evidence={("notes", "knowledge/notes.md", ("Root", "chunk-3"))},
        source_ids=["notes"],
    )

    assert result.vector.recall_at[1] == 0.0
    assert result.reranked.recall_at[1] == 1.0
    assert result.reranker_minus_vector["recall_at_1"] == 1.0
    assert result.promotions.promoted == 1
    assert result.promotions.cases[0].rank_change == 2
    assert result.recall_at_candidate_invariant is True
    assert result.hard_negatives[0].top1_score == pytest.approx(0.9)
    assert result.relevant_reranker_scores.maximum == pytest.approx(0.9)


def test_reranked_real_vector_search_is_read_only_and_survives_reopen(tmp_path: Path) -> None:
    database = Database(tmp_path / "reranking.db")
    connection = database.connect()
    embedding = FakeEmbeddingProvider()
    KnowledgeIndexer(connection, embedding, _profile()).build(
        [_source_input("notes", [("first", "order-6"), ("second", "order-7")])]
    )
    connection.close()
    reopened = database.connect(read_only=True, initialize=False)
    vector = VectorRetriever(
        reopened,
        embedding,
        _profile(),
        config=VectorRetrievalConfig(max_top_k=20, maximum_candidate_k=20),
    )
    provider = FakeRerankerProvider([0.1, 0.9])
    reranked = RerankedRetriever(
        vector,
        provider,
        config=RerankerRetrievalConfig(candidate_k=10, maximum_top_k=10),
    )
    before = reopened.total_changes

    response = reranked.retrieve(RetrievalQuery(text="query", top_k=2))

    assert len(response.results) == 2
    assert all(item.visibility == Visibility.PUBLIC for item in response.results)
    assert reopened.total_changes == before
    reopened.close()
