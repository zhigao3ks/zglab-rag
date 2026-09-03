from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, model_validator
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
    # Managed Git checkouts live outside the deployed application tree in
    # production.  Sources without acquisition metadata continue using their
    # legacy relative local_path.
    source_checkout_root: Path = Path("runtime/sources")
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
    api_cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:8000", "http://127.0.0.1:8000"]
    )
    api_sse_heartbeat_seconds: float = 15.0
    api_trusted_proxy_ips: list[str] = Field(default_factory=lambda: ["127.0.0.1", "::1"])
    # Phase 11: retire the anonymous v1 ask endpoints (410 Gone). Keep
    # false for local regression of Phase 9 contracts; production sets true
    # during the Phase 11 migration.
    api_v1_retired: bool = False

    # Localhost-only embedding capability shared with trusted sibling services.
    # The secret deliberately has no default and is never rendered in responses/logs.
    internal_embedding_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "INTERNAL_EMBEDDING_TOKEN", "ZGLAB_RAG_INTERNAL_EMBEDDING_TOKEN"
        ),
    )
    internal_embedding_timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    internal_embedding_max_texts: int = Field(default=64, ge=1, le=256)
    internal_embedding_max_text_chars: int = Field(default=8000, ge=1, le=32000)
    internal_embedding_max_request_body_bytes: int = Field(
        default=512 * 1024, ge=1024, le=2 * 1024 * 1024
    )

    # Phase 11 Authentication & Access Control
    auth_database_path: Path = Path("runtime/auth.db")

    # Phase 15A1 storage foundation. This path is intentionally not wired
    # into API/runtime behavior until a later explicitly authorized slice.
    conversation_database_path: Path = Path("runtime/conversation.db")

    # Phase 15B: bounded recent completed turns only.
    # Phase 15C: extended with summary + relevant historical + unified byte budget.
    conversation_context_max_turns: int = Field(default=4, ge=1, le=12)
    conversation_context_max_chars: int = Field(default=6000, ge=200, le=24000)
    conversation_context_max_message_chars: int = Field(default=2000, ge=100, le=8000)
    conversation_context_max_bytes: int = Field(default=18000, ge=1000, le=72000)

    # Relevant historical turns (Phase 15C)
    conversation_context_relevant_turns: int = Field(default=2, ge=0, le=8)
    conversation_context_relevant_max_chars: int = Field(default=1200, ge=0, le=4800)
    conversation_context_history_scan_messages: int = Field(default=160, ge=20, le=800)

    # Retrieval query budget (Phase 15C)
    conversation_context_retrieval_query_max_chars: int = Field(default=3000, ge=500, le=8000)
    conversation_context_retrieval_query_max_bytes: int = Field(default=9000, ge=1500, le=24000)

    # Summary generation (Phase 15C)
    conversation_summary_enabled: bool = False
    conversation_summary_trigger_new_turns: int = Field(default=4, ge=2, le=16)
    conversation_summary_max_batch_turns: int = Field(default=8, ge=2, le=32)
    conversation_summary_source_max_chars: int = Field(default=12000, ge=2000, le=32000)
    conversation_summary_source_max_bytes: int = Field(default=36000, ge=6000, le=96000)
    conversation_summary_max_chars: int = Field(default=1600, ge=200, le=4000)

    # Phase 15D: bounded, per-conversation typed resource reuse.  This is
    # intentionally opt-in so deploying the schema never changes behavior.
    session_resource_reuse_enabled: bool = False
    session_resource_max_items: int = Field(default=48, ge=1, le=256)
    session_resource_max_bytes: int = Field(default=524288, ge=4096, le=4 * 1024 * 1024)
    session_resource_max_item_bytes: int = Field(default=65536, ge=1024, le=512 * 1024)
    session_personal_ttl_seconds: int = Field(default=21600, ge=60, le=86400)
    session_web_ttl_seconds: int = Field(default=300, ge=30, le=3600)
    session_tool_ttl_seconds: int = Field(default=86400, ge=60, le=7 * 86400)
    # Public origin used to build activation URLs in CLI output and to
    # validate Origin/Referer headers; set to https://ask.zglab.fun in prod.
    auth_public_base_url: str = "http://localhost:8000"
    # Explicit origin allowlist for Origin/Referer validation. Empty means
    # "same host as auth_public_base_url".
    auth_allowed_origins: list[str] = Field(default_factory=list)
    auth_cookie_name: str = "__Host-zglab_session"
    # __Host- cookies require Secure; tests/local HTTP may disable it.
    auth_cookie_secure: bool = True
    auth_session_idle_timeout_hours: float = 24 * 7
    auth_session_absolute_timeout_hours: float = 24 * 30
    auth_activation_token_hours: float = 24.0
    auth_reset_token_hours: float = 24.0
    auth_password_min_length: int = 12
    auth_password_max_length: int = 128
    auth_login_per_ip_attempts: int = 10
    auth_login_per_ip_window_seconds: int = 600
    auth_login_per_username_attempts: int = 5
    auth_login_per_username_window_seconds: int = 900
    auth_user_requests_per_minute: int = 10
    auth_user_requests_per_day: int = 100

    # Capability kill switches. LLM_ENABLED=false keeps landing/login/auth
    # working while ask endpoints refuse to call the external provider.
    llm_enabled: bool = True

    # Phase 12B Web Research Core. Fail-closed by default: the pipeline is
    # not wired to any public endpoint yet and stays disabled until the
    # Phase 12D product acceptance. The search API key only ever comes from
    # environment/config — never code, tests, docs or logs.
    web_research_enabled: bool = False
    search_provider: str = "tavily"
    search_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("ZGLAB_RAG_SEARCH_API_KEY", "SEARCH_API_KEY")
    )
    search_timeout_seconds: float = 8.0
    research_max_search_results: int = 6
    research_max_fetch_candidates: int = 4
    research_max_redirects: int = 3
    research_fetch_timeout_seconds: float = 8.0
    research_overall_timeout_seconds: float = 30.0
    research_max_response_bytes: int = 1_572_864
    research_max_extracted_chars: int = 8_000

    # Phase 12D product cost boundary: web research gets its own quota
    # bucket (never shared with ordinary personal asks), a dedicated
    # concurrency limit (conservative on the 2 vCPU / 2 GiB instance) and
    # a server-side permission policy.
    web_research_requests_per_minute: int = 3
    web_research_requests_per_day: int = 20
    web_research_concurrency: int = Field(default=1, ge=1, le=4)
    web_research_admin_only: bool = False

    # Phase 13C MCP Tool Runtime (host side). Fail-closed default: disabled.
    # command/args/cwd are owner deployment configuration, never user input;
    # the child process receives only a minimal, secret-free environment.
    mcp_enabled: bool = False
    mcp_server_command: str = "node"
    mcp_server_args: list[str] = Field(default_factory=lambda: ["dist-mcp/cli.js"])
    mcp_server_cwd: str | None = None
    mcp_expected_server_name: str = "zglab-tools-mcp"
    mcp_startup_timeout_seconds: float = 10.0
    mcp_call_timeout_seconds: float = 2.0
    mcp_shutdown_timeout_seconds: float = 5.0
    # Match the Tool Core payload contract (256 KiB) and remain below the
    # Node stdio transport's 1 MiB frame ceiling. The host must not quietly
    # admit a materially larger payload than the deterministic tool boundary.
    mcp_max_request_bytes: int = 256 * 1024
    mcp_max_response_bytes: int = 256 * 1024
    mcp_max_concurrent_calls: int = Field(default=1, ge=1, le=4)

    # Phase 14D Agent product boundary. Agent remains opt-in and disabled
    # by default; it has a distinct user quota because one request may fan
    # out to multiple already-bounded capabilities.
    agent_enabled: bool = False
    agent_requests_per_minute: int = 2
    agent_requests_per_day: int = 10
    agent_concurrency: int = Field(default=1, ge=1, le=1)
    agent_overall_deadline_seconds: float = Field(default=60.0, gt=0, le=90)

    @model_validator(mode="after")
    def _validate_auth_cookie_security(self) -> Settings:
        """Refuse the insecure __Host- + Secure=false combination.

        Browsers reject a __Host- cookie without Secure, so this
        misconfiguration would silently break authentication — and it is
        exactly the weak-cookie trap production must never fall into.
        Local HTTP development must use a plain dev-only cookie name
        (e.g. zglab_session_dev) together with Secure=false.
        """
        if self.auth_cookie_name.startswith("__Host-") and not self.auth_cookie_secure:
            raise ValueError(
                "auth_cookie_name uses the __Host- prefix, which requires "
                "auth_cookie_secure=true; refusing insecure __Host- cookie "
                "configuration. For local HTTP development set a plain "
                "cookie name (e.g. zglab_session_dev) instead."
            )
        return self

    @property
    def llm_provider_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)


def get_settings() -> Settings:
    return Settings()
