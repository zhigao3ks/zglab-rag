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
    sources_config: Path = Path("config/sources.yaml")
    default_visibility: str = "public"

    chunk_target_size: int = 700
    chunk_max_size: int = 1200
    chunk_overlap: int = 120


def get_settings() -> Settings:
    return Settings()
