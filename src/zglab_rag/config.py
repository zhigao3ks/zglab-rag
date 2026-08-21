from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ZGLAB_RAG_",
        extra="ignore",
    )

    env: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"

    data_dir: Path = Path("runtime")
    database_path: Path = Path("runtime/knowledge.db")
    backup_dir: Path = Path("runtime/backups")
    backup_retain_count: int = 7
    sources_config: Path = Path("config/sources.yaml")
    default_visibility: str = "public"

    chunk_target_size: int = 700
    chunk_max_size: int = 1200
    chunk_overlap: int = 120

    retrieval_default_top_k: int = 5
    retrieval_max_top_k: int = 50
    retrieval_candidate_factor: int = 4
    retrieval_minimum_candidate_k: int = 20
    retrieval_maximum_candidate_k: int = 1000
    hybrid_vector_candidate_k: int = 50
    hybrid_lexical_candidate_k: int = 50
    hybrid_rrf_k: int = 60
    hybrid_vector_weight: float = 1.0
    hybrid_lexical_weight: float = 1.0
    reranker_candidate_k: int = 20
    reranker_default_top_k: int = 5

    llm_base_url: str | None = Field(
        default=None, validation_alias=AliasChoices("ZGLAB_RAG_LLM_BASE_URL", "LLM_BASE_URL")
    )
    llm_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("ZGLAB_RAG_LLM_API_KEY", "LLM_API_KEY")
    )
    llm_model: str | None = Field(
        default=None, validation_alias=AliasChoices("ZGLAB_RAG_LLM_MODEL", "LLM_MODEL")
    )
    llm_timeout_seconds: float = 60.0
    generation_retrieval_top_k: int = 5
    generation_max_evidence_items: int = 5
    generation_max_context_chars: int = 6000

    # Phase 9A Public API configuration
    api_question_min_length: int = 1
    api_question_max_length: int = 1000
    api_request_timeout_seconds: float = 90.0
    api_max_concurrent_requests: int = 1
    api_rate_limit_requests: int = 10
    api_rate_limit_window_seconds: int = 60
    api_max_request_body_bytes: int = 16 * 1024  # 16 KiB
    api_cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:8000", "http://127.0.0.1:8000"])
    api_sse_heartbeat_seconds: float = 15.0
    api_trusted_proxy_ips: list[str] = Field(default_factory=lambda: ["127.0.0.1", "::1"])

    @property
    def llm_provider_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)


def get_settings() -> Settings:
    return Settings()
