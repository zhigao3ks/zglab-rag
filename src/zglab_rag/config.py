from pathlib import Path

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


def get_settings() -> Settings:
    return Settings()
