from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from zglab_rag.domain.models import KnowledgeChunk, Scope, Visibility
from zglab_rag.embeddings.config import (
    EmbeddingBackend,
    EmbeddingModelConfig,
    QueryMode,
)
from zglab_rag.embeddings.sentence_transformer import (
    BGE_ZH_QUERY_INSTRUCTION,
    SentenceTransformerEmbeddingProvider,
    _default_model_factory,
)
from zglab_rag.evaluation.benchmark import run_embedding_benchmark
from zglab_rag.evaluation.composition import TextComposition, compose_document_text
from zglab_rag.evaluation.dataset import (
    EvaluationQuery,
    LoadedEvaluationDataset,
    QueryCategory,
    RelevantTarget,
    RetrievalEvaluationDataset,
    load_evaluation_dataset,
)
from zglab_rag.evaluation.retrieval import (
    compute_retrieval_metrics,
    cosine_similarity_matrix,
    rank_by_cosine,
)


def _chunk(
    chunk_id: str,
    *,
    content: str = "content",
    source_path: str = "knowledge/example.md",
    section_path: list[str] | None = None,
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        source_id="notes",
        scope=Scope.KNOWLEDGE,
        visibility=Visibility.PUBLIC,
        priority=80,
        title="Example title",
        section_path=section_path or ["Root", "Section"],
        chunk_index=0,
        content=content,
        content_hash=f"hash-{chunk_id}",
        char_count=len(content),
        source_path=source_path,
    )


def _target(section: str = "Section") -> RelevantTarget:
    return RelevantTarget(
        source_id="notes",
        source_path="knowledge/example.md",
        section_path=["Root", section],
    )


def _model_config(query_mode: QueryMode = QueryMode.BGE_ZH_INSTRUCTION):
    return EmbeddingModelConfig(
        id="fake",
        model_name="example/fake",
        backend=EmbeddingBackend.SENTENCE_TRANSFORMERS,
        query_mode=query_mode,
        normalize=True,
        max_length=512,
        enabled=True,
    )


def test_evaluation_dataset_parsing_and_distribution(tmp_path: Path) -> None:
    dataset_path = tmp_path / "retrieval.yaml"
    dataset_path.write_text(
        """
version: 1
queries:
  - id: query-1
    query: How does it work?
    category: knowledge
    relevant:
      - source_id: notes
        source_path: knowledge/example.md
        section_path: [Root, Section]
  - id: negative-1
    query: What is not covered?
    category: hard_negative
    relevant: []
""".strip(),
        encoding="utf-8",
    )

    loaded = load_evaluation_dataset(dataset_path)

    assert loaded.dataset.version == 1
    assert loaded.dataset.category_distribution()["knowledge"] == 1
    assert loaded.dataset.category_distribution()["hard_negative"] == 1
    assert len(loaded.sha256) == 64


def test_tracked_evaluation_dataset_has_expected_size_and_categories() -> None:
    loaded = load_evaluation_dataset("evaluation/retrieval.yaml")

    assert len(loaded.dataset.queries) == 50
    assert loaded.dataset.category_distribution() == {
        "identity": 8,
        "knowledge": 16,
        "project": 10,
        "problem": 8,
        "mixed_technical": 5,
        "hard_negative": 3,
    }


def test_evaluation_dataset_rejects_unlabelled_scored_query(tmp_path: Path) -> None:
    dataset_path = tmp_path / "invalid.yaml"
    dataset_path.write_text(
        "version: 1\nqueries:\n  - id: q\n    query: q\n    category: knowledge\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="scored queries require"):
        load_evaluation_dataset(dataset_path)


def test_relevant_target_matching_supports_a_section_subtree() -> None:
    chunk = _chunk("chunk-a")

    assert _target().matches(chunk)
    assert RelevantTarget(
        source_id="notes",
        source_path=chunk.source_path,
        section_path=["Root"],
    ).matches(chunk)
    assert not _target("Other").matches(chunk)
    assert not RelevantTarget(
        source_id="other",
        source_path=chunk.source_path,
        section_path=chunk.section_path,
    ).matches(chunk)


def test_cosine_similarity_and_stable_ranking() -> None:
    chunks = [_chunk("chunk-b"), _chunk("chunk-a"), _chunk("chunk-c")]
    documents = np.asarray([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    queries = np.asarray([[1.0, 0.0]], dtype=np.float32)

    similarities = cosine_similarity_matrix(queries, documents)
    rankings = rank_by_cosine(queries, documents, chunks)

    assert similarities.tolist() == [[1.0, 1.0, 0.0]]
    assert rankings == [[1, 0, 2]]


def test_recall_and_mrr_support_multiple_targets() -> None:
    chunks = [
        _chunk("a", section_path=["Root", "A"]),
        _chunk("b", section_path=["Root", "B"]),
        _chunk("c", section_path=["Root", "C"]),
    ]
    queries = [
        EvaluationQuery(
            id="q1", query="a", category=QueryCategory.KNOWLEDGE, relevant=[_target("A")]
        ),
        EvaluationQuery(
            id="q2",
            query="b and c",
            category=QueryCategory.KNOWLEDGE,
            relevant=[_target("B"), _target("C")],
        ),
    ]

    metrics = compute_retrieval_metrics(
        queries,
        rankings=[[0, 1, 2], [0, 1, 2]],
        chunks=chunks,
        cutoffs=(1, 3),
    )

    assert metrics.recall_at == {1: 0.5, 3: 1.0}
    assert metrics.mrr == 0.75


def test_default_recall_cutoffs_include_twenty_and_thirty() -> None:
    chunks = [_chunk("a", section_path=["Root", "A"])]
    query = EvaluationQuery(
        id="q1",
        query="a",
        category=QueryCategory.KNOWLEDGE,
        relevant=[_target("A")],
    )

    metrics = compute_retrieval_metrics([query], [[0]], chunks)

    assert metrics.recall_at == {1: 1.0, 3: 1.0, 5: 1.0, 10: 1.0, 20: 1.0, 30: 1.0}


def test_contextual_text_composition_preserves_hierarchy() -> None:
    chunk = _chunk("chunk-a", content="Body", section_path=["Root", "Child"])

    assert compose_document_text(chunk, TextComposition.CONTENT_ONLY) == "Body"
    assert compose_document_text(chunk, TextComposition.CONTEXTUAL) == (
        "Title: Example title\nSection: Root > Child\n\nBody"
    )


class RecordingModel:
    max_seq_length = 0

    def __init__(self) -> None:
        self.document_calls: list[tuple[list[str], dict[str, object]]] = []
        self.query_calls: list[tuple[list[str], dict[str, object]]] = []

    def get_sentence_embedding_dimension(self) -> int:
        return 2

    def encode_document(self, sentences, **kwargs):
        self.document_calls.append((sentences, kwargs))
        return np.ones((len(sentences), 2), dtype=np.float32)

    def encode_query(self, sentences, **kwargs):
        self.query_calls.append((sentences, kwargs))
        return np.ones((len(sentences), 2), dtype=np.float32)


@pytest.mark.parametrize(
    ("query_mode", "expected_query", "expected_document", "expected_prompt"),
    [
        (
            QueryMode.BGE_ZH_INSTRUCTION,
            f"{BGE_ZH_QUERY_INSTRUCTION}问题",
            "文档",
            None,
        ),
        (QueryMode.E5_PREFIX, "query: 问题", "passage: 文档", None),
        (QueryMode.MODEL_QUERY_PROMPT, "问题", "文档", "query"),
    ],
)
def test_sentence_transformer_adapter_separates_query_and_document_modes(
    query_mode: QueryMode,
    expected_query: str,
    expected_document: str,
    expected_prompt: str | None,
) -> None:
    model = RecordingModel()
    provider = SentenceTransformerEmbeddingProvider(
        _model_config(query_mode),
        device="cpu",
        model_factory=lambda _name, _device: model,
    )

    provider.encode_documents(["文档"])
    provider.encode_queries(["问题"])

    assert model.document_calls[0][0] == [expected_document]
    assert model.query_calls[0][0] == [expected_query]
    assert model.query_calls[0][1].get("prompt_name") == expected_prompt
    assert model.max_seq_length == 512


def test_offline_model_factory_resolves_cached_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    class FakeSentenceTransformer:
        def __init__(self, model_name: str, *, device: str) -> None:
            calls.append((model_name, device))

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )
    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=lambda name, local_files_only: "/models/bge"),
    )

    _default_model_factory("BAAI/bge-small-zh-v1.5", "cpu")

    assert calls == [("/models/bge", "cpu")]


class FakeEmbeddingProvider:
    model_name = "example/fake"
    dimension = 2
    device = "cpu"

    def __init__(self) -> None:
        self.document_calls: list[list[str]] = []
        self.query_calls: list[list[str]] = []

    def encode_documents(self, texts):
        self.document_calls.append(list(texts))
        return np.asarray(
            [[1.0, 0.0] if "alpha" in text else [0.0, 1.0] for text in texts],
            dtype=np.float32,
        )

    def encode_queries(self, texts):
        self.query_calls.append(list(texts))
        return np.asarray(
            [[1.0, 0.0] if "alpha" in text else [0.0, 1.0] for text in texts],
            dtype=np.float32,
        )


def test_benchmark_metadata_and_quality_are_stable() -> None:
    chunks = [
        _chunk("alpha", content="alpha", section_path=["Root", "A"]),
        _chunk("beta", content="beta", section_path=["Root", "B"]),
    ]
    knowledge_query = EvaluationQuery(
        id="q1",
        query="alpha",
        category=QueryCategory.KNOWLEDGE,
        relevant=[_target("A")],
    )
    identity_query = EvaluationQuery(
        id="q2",
        query="beta",
        category=QueryCategory.IDENTITY,
        relevant=[_target("B")],
    )
    loaded = LoadedEvaluationDataset(
        dataset=RetrievalEvaluationDataset(
            version=3,
            queries=[knowledge_query, identity_query],
        ),
        sha256="a" * 64,
        path=Path("evaluation/retrieval.yaml"),
    )

    def run_once():
        provider = FakeEmbeddingProvider()
        result = run_embedding_benchmark(
            chunks=chunks,
            dataset=loaded,
            provider=provider,
            model_config=_model_config(),
            composition=TextComposition.CONTEXTUAL,
            source_revisions={"notes": "revision-1"},
            chunking_config={"target_size": 700, "max_size": 1200, "overlap": 120},
            timestamp="2026-01-01T00:00:00+00:00",
        )
        return result, provider

    first, first_provider = run_once()
    second, _ = run_once()

    assert first.quality == second.quality
    assert first.metadata == second.metadata
    assert first.quality.recall_at_1 == 1.0
    assert first.quality.recall_at_20 == 1.0
    assert first.quality.recall_at_30 == 1.0
    assert first.quality.mrr == 1.0
    assert first.quality.category_breakdown["knowledge"].query_count == 1
    assert first.quality.category_breakdown["knowledge"].recall_at_20 == 1.0
    assert first.quality.category_breakdown["identity"].query_count == 1
    assert first.metadata.dataset_version == 3
    assert first.metadata.dataset_sha256 == "a" * 64
    assert first.metadata.source_revisions == {"notes": "revision-1"}
    assert first_provider.document_calls[0][0].startswith("Title: Example title")
    assert first_provider.query_calls == [["alpha"], ["beta"]]


def test_benchmark_rejects_non_public_chunks() -> None:
    private_chunk = _chunk("private").model_copy(update={"visibility": Visibility.PRIVATE})
    loaded = LoadedEvaluationDataset(
        dataset=RetrievalEvaluationDataset(
            version=1,
            queries=[
                EvaluationQuery(
                    id="q1",
                    query="content",
                    category=QueryCategory.KNOWLEDGE,
                    relevant=[_target()],
                )
            ],
        ),
        sha256="b" * 64,
        path=Path("evaluation/retrieval.yaml"),
    )

    with pytest.raises(ValueError, match="non-public"):
        run_embedding_benchmark(
            chunks=[private_chunk],
            dataset=loaded,
            provider=FakeEmbeddingProvider(),
            model_config=_model_config(),
            composition=TextComposition.CONTENT_ONLY,
            source_revisions={"notes": "revision-1"},
            chunking_config={"target_size": 700, "max_size": 1200, "overlap": 120},
        )
