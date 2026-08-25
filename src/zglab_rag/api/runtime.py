"""Production application runtime implementation.

This module provides the real ApplicationRuntime that loads the embedding
model, configures the LLM provider, and manages database connections.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from zglab_rag.application.runtime import (
    EmbeddingComponents,
    build_embedding_components,
    build_llm_provider,
)
from zglab_rag.config import Settings, get_settings
from zglab_rag.generation.contracts import GenerationProvider
from zglab_rag.generation.service import GroundedAnswerService
from zglab_rag.storage.database import Database


class ProductionRuntime:
    """Production runtime that holds long-lived components.

    The embedding model is loaded once at startup and reused across requests.
    Database connections are request-scoped and read-only.
    """

    def __init__(
        self,
        settings: Settings,
        embedding_components: EmbeddingComponents,
        llm_provider: GenerationProvider,
        database: Database,
    ) -> None:
        self.settings = settings
        self.embedding_components = embedding_components
        self.llm_provider = llm_provider
        self.database = database

    @classmethod
    def create(
        cls,
        settings: Settings | None = None,
        *,
        models_config: Path | None = None,
        device: str = "cpu",
        batch_size: int = 32,
    ) -> ProductionRuntime:
        """Create a new production runtime, loading embedding model."""
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

    @contextmanager
    def request_connection(self):
        """Yield a request-scoped read-only SQLite connection."""
        connection = None
        try:
            connection = self.database.connect(read_only=True, initialize=False)
            yield connection
        finally:
            if connection is not None:
                connection.close()

    def create_service(self, connection: sqlite3.Connection) -> GroundedAnswerService:
        """Create a request-scoped GroundedAnswerService."""
        from zglab_rag.application.runtime import build_generation_service

        return build_generation_service(
            connection,
            self.embedding_components,
            self.llm_provider,
            settings=self.settings,
        )

    def verify_ready(self) -> None:
        """Verify dependencies required before accepting public requests.

        The embedding provider has already been constructed during startup. This method
        checks the persistent index with a read-only connection and validates that the
        LLM configuration is complete without making an external provider request.

        With the Phase 11 LLM kill switch active the LLM configuration check is
        skipped: the service stays ready for landing/login/auth while ask endpoints
        refuse generation with SERVICE_DISABLED.
        """
        if self.settings.llm_enabled and not self.settings.llm_provider_configured:
            raise RuntimeError("LLM provider configuration is incomplete")
        connection = self.database.connect(read_only=True, initialize=False)
        try:
            self.database.versions(connection)
        finally:
            connection.close()
