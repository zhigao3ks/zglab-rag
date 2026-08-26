"""Shared application runtime factory for CLI and HTTP API.

This module provides a single source of truth for constructing the
GroundedAnswerService and its dependencies. Both the generation CLI
and the public FastAPI API use this factory to avoid configuration
drift.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Protocol

from zglab_rag.config import Settings, get_settings
from zglab_rag.embeddings.sentence_transformer import SentenceTransformerEmbeddingProvider
from zglab_rag.generation.context import ContextBudget
from zglab_rag.generation.contracts import GenerationProvider
from zglab_rag.generation.openai_provider import (
    OpenAICompatibleConfig,
    OpenAICompatibleProvider,
)
from zglab_rag.generation.service import GroundedAnswerService, GroundedGenerationConfig
from zglab_rag.indexing.profile import load_active_embedding_profile
from zglab_rag.reranking.config import RerankerModelRegistry
from zglab_rag.reranking.cross_encoder import CrossEncoderRerankerProvider
from zglab_rag.reranking.service import RerankedRetriever, RerankerRetrievalConfig
from zglab_rag.retrieval.cli import retrieval_config
from zglab_rag.retrieval.contracts import RetrievalQuery, RetrievalResponse
from zglab_rag.retrieval.vector import VectorRetriever
from zglab_rag.storage.database import Database


class Retriever(Protocol):
    """Protocol for retrieval backends."""

    def retrieve(self, query: RetrievalQuery) -> RetrievalResponse: ...


@dataclass
class EmbeddingComponents:
    """Embedding provider and profile loaded from configuration."""

    provider: SentenceTransformerEmbeddingProvider
    profile: object  # EmbeddingProfile
    model_config: object  # EmbeddingModelConfig


def build_embedding_components(
    models_config: Path,
    *,
    device: str = "cpu",
    batch_size: int = 32,
) -> EmbeddingComponents:
    """Load embedding profile and construct the provider.

    This is an expensive operation (model loading); the result should be
    reused across requests, not recreated per-request.
    """
    profile, model_config = load_active_embedding_profile(models_config)
    provider = SentenceTransformerEmbeddingProvider(
        model_config,
        device=device,
        batch_size=batch_size,
    )
    return EmbeddingComponents(provider=provider, profile=profile, model_config=model_config)


def build_vector_retriever(
    connection,
    embedding_provider: SentenceTransformerEmbeddingProvider,
    profile,
    *,
    model_config,
    settings: Settings,
) -> VectorRetriever:
    """Construct a read-only VectorRetriever for the given connection."""
    return VectorRetriever(
        connection,
        embedding_provider,
        profile,
        model_config=model_config,
        config=retrieval_config(settings),
    )


def build_generation_retriever(
    mode: str,
    *,
    connection,
    settings: Settings,
    embedding_components: EmbeddingComponents,
    candidate_k: int | None = None,
    reranker_models_config: Path = Path("config/reranker-models.yaml"),
    reranker_model: str = "mmarco-mMiniLMv2-L12-H384-v1",
    reranker_model_path: Path | None = None,
    device: str = "cpu",
) -> Retriever:
    """Construct a retriever for generation (vector or reranked mode).

    This is the shared factory used by both CLI and HTTP API.
    """
    vector = VectorRetriever(
        connection,
        embedding_components.provider,
        embedding_components.profile,
        model_config=embedding_components.model_config,
        config=retrieval_config(settings),
    )
    if mode == "vector":
        return vector
    candidate_k = candidate_k or settings.reranker_candidate_k
    reranker_model_config = RerankerModelRegistry.from_yaml(reranker_models_config).get_enabled(
        reranker_model
    )
    reranker_provider = CrossEncoderRerankerProvider(
        reranker_model_config,
        device=device,
        model_path=reranker_model_path,
    )
    return RerankedRetriever(
        vector,
        reranker_provider,
        config=RerankerRetrievalConfig(
            default_top_k=min(settings.generation_retrieval_top_k, candidate_k),
            maximum_top_k=candidate_k,
            candidate_k=candidate_k,
        ),
    )


def build_llm_provider(settings: Settings) -> OpenAICompatibleProvider:
    """Construct the OpenAI-compatible LLM provider from settings."""
    return OpenAICompatibleProvider(
        OpenAICompatibleConfig(
            base_url=settings.llm_base_url or "",
            api_key=settings.llm_api_key or "",
            model=settings.llm_model or "",
            timeout_seconds=settings.llm_timeout_seconds,
        )
    )


def build_generation_service(
    connection,
    embedding_components: EmbeddingComponents,
    llm_provider: GenerationProvider,
    *,
    settings: Settings,
    mode: str = "vector",
) -> GroundedAnswerService:
    """Construct a fully configured GroundedAnswerService.

    This is the primary entry point for both CLI and HTTP API.
    """
    retriever = build_generation_retriever(
        mode,
        connection=connection,
        settings=settings,
        embedding_components=embedding_components,
    )
    return GroundedAnswerService(
        retriever,
        llm_provider,
        config=GroundedGenerationConfig(
            retrieval_mode=mode,
            retrieval_top_k=settings.generation_retrieval_top_k,
            budget=ContextBudget(
                max_evidence_items=settings.generation_max_evidence_items,
                max_context_chars=settings.generation_max_context_chars,
            ),
        ),
    )


@dataclass
class ApplicationRuntime:
    """Holds long-lived application components.

    The runtime is created once at application startup and shared across
    requests. It holds the embedding model (expensive to load) and the
    LLM provider configuration. Database connections are request-scoped.
    """

    settings: Settings
    embedding_components: EmbeddingComponents
    llm_provider: GenerationProvider
    database: Database

    @classmethod
    def create(
        cls,
        settings: Settings | None = None,
        *,
        models_config: Path | None = None,
        device: str = "cpu",
        batch_size: int = 32,
    ) -> ApplicationRuntime:
        """Create a new runtime, loading embedding model and configuring LLM."""
        settings = settings or get_settings()
        embedding_components = build_embedding_components(
            models_config or Path("config/embedding-models.yaml"),
            device=device,
            batch_size=batch_size,
        )
        llm_provider = build_llm_provider(settings)
        database = Database(settings.database_path)
        return cls(
            settings=settings,
            embedding_components=embedding_components,
            llm_provider=llm_provider,
            database=database,
        )

    def create_service(self, connection) -> GroundedAnswerService:
        """Create a request-scoped GroundedAnswerService.

        The connection should be a request-scoped SQLite connection.
        """
        return build_generation_service(
            connection,
            self.embedding_components,
            self.llm_provider,
            settings=self.settings,
        )

    @contextmanager
    def request_connection(self):
        """Yield a request-scoped read-only SQLite connection.

        Mirrors ProductionRuntime so both runtimes satisfy the capability
        layer's KnowledgePipelineRuntime protocol.
        """
        connection = None
        try:
            connection = self.database.connect(read_only=True, initialize=False)
            yield connection
        finally:
            if connection is not None:
                connection.close()

    @cached_property
    def capability_registry(self):
        """Phase 12A capability boundary wrapping this runtime.

        Lazy import keeps the CLI path free of capability imports until the
        boundary is actually used; the registry itself is app-scoped.
        """
        from zglab_rag.capabilities.personal_knowledge import build_capability_registry

        return build_capability_registry(self)
