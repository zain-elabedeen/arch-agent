"""
Application settings (``ARCHAGENT_*`` env vars).

Environment-specific defaults and optional LLM keys for the reasoning agent live
here; ``main.recommend`` clears the settings cache and passes fresh ``Settings``
into ``build_graph`` each request.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Small surface area for the MVP: pattern store mode, paths, logging, LLM toggles.

    ``pattern_store=postgres`` is reserved; only ``filesystem`` is implemented today.
    """

    model_config = SettingsConfigDict(
        env_prefix="ARCHAGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["dev", "test", "prod"] = "dev"
    service_role: Literal["api", "ops", "ingest", "worker", "job", "collector"] = "api"

    # Product control plane. Local and production runtimes use Postgres for
    # schema parity; tests can still inject SQLite stores directly.
    auth_mode: Literal["dev", "workos"] = "dev"
    product_database_url: str = "postgresql+psycopg://archagent:archagent@localhost:5432/archagent"
    storage_backend: Literal["filesystem", "gcs"] = "filesystem"
    local_storage_path: str = "/tmp/archagent-storage"
    task_dispatcher: Literal["inline", "cloud_tasks"] = "inline"
    document_scan_mode: Literal["dev_allow", "clamav"] = "dev_allow"
    collector_registration_endpoint: str = "https://api.example.invalid"
    collector_ingest_endpoint: str = "https://ingest.example.invalid"
    collector_registration_token: Optional[str] = None
    collector_credential_file: str = "/var/lib/archagent/credential"
    collector_retry_queue_file: str = "/var/lib/archagent/retry-queue.json"
    collector_retry_queue_size: int = 20
    collector_request_attempts: int = 3
    collector_retry_initial_sec: float = 1.0
    gcp_region: str = "europe-west1"
    gcp_storage_bucket: Optional[str] = None
    gcp_quarantine_bucket: Optional[str] = None
    gcp_tasks_queue: Optional[str] = None
    gcp_tasks_target_url: Optional[str] = None
    gcp_tasks_service_account: Optional[str] = None
    gcp_tasks_oidc_audience: Optional[str] = None
    scanner_service_url: Optional[str] = None
    scanner_service_token: Optional[str] = None
    workos_api_key: Optional[str] = None
    workos_client_id: Optional[str] = None
    workos_cookie_password: Optional[str] = None
    workos_webhook_secret: Optional[str] = None
    workos_webhook_path_token: Optional[str] = None
    workos_self_serve_role_slug: Optional[str] = None
    workos_redirect_uri: str = "http://localhost:8000/auth/callback"
    workos_sign_in_endpoint: str = "http://localhost:8000/auth/login"
    workos_sign_out_redirect: str = "http://localhost:8000/auth/signed-out"
    workos_post_login_redirect: str = "http://localhost:8000/v1/session"
    workos_auto_provision_signups: bool = True
    csrf_secret: Optional[str] = None
    cookie_secure: bool = True
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    session_cookie_max_age_sec: int = 2_592_000
    csrf_token_ttl_sec: int = 2_592_000
    allowed_origins: str = (
        "http://localhost:3000,http://localhost:5173,"
        "http://localhost:8080,http://localhost:8081,"
        "http://127.0.0.1:8080,http://127.0.0.1:8081,"
        "https://lovable.dev,https://app.archagent.de"
    )
    allowed_origin_regex: Optional[str] = None
    internal_user_ids: str = ""
    collector_registration_ttl_sec: int = 900
    collector_credential_ttl_sec: int = 2_592_000

    # Storage mode for the pattern catalog. Postgres support can be added by
    # implementing the repository interface in app/services/pattern_loader.py.
    pattern_store: Literal["filesystem", "postgres"] = "filesystem"

    # When pattern_store="postgres"
    postgres_dsn: Optional[str] = None

    # Kubernetes connector worker: poll interval (seconds) when running ``worker`` module.
    k8s_poll_interval_sec: int = 45
    ingestion_connectors: str = "kubernetes,logs"

    # Logs connector ingestion. The MVP source is Kubernetes pod logs, while the
    # normalized model is source-neutral for future providers.
    logs_enabled: bool = True
    log_window_grace_sec: int = 10
    log_tail_lines: int = 500

    # Optional log-analysis agent. This runs inside the recommendation pipeline,
    # not inside the logs connector/parser, and cannot create decisions directly.
    log_llm_enabled: bool = True
    log_sample_limit: int = 20
    log_llm_model: Optional[str] = None
    log_llm_max_output_tokens: int = 512

    # Namespace filters for local/dev clusters. Empty include means "all allowed
    # namespaces"; excludes remove Kubernetes/platform namespaces by default.
    k8s_include_namespaces: str = ""
    k8s_exclude_namespaces: str = "kube-system,kube-public,kube-node-lease,kubernetes-dashboard"

    # Auto-create connector tables on API/worker startup when ``postgres_dsn`` is set.
    k8s_auto_migrate: bool = True

    # Pattern catalog location for filesystem mode
    patterns_path: str = "agent/app/patterns"

    # Architecture knowledge RAG. Disabled by default so deterministic MVP
    # behavior is unchanged until a knowledge index is configured.
    rag_enabled: bool = False
    rag_store: Literal["postgres"] = "postgres"
    rag_knowledge_path: str = "agent/app/knowledge_sources"
    rag_embedding_provider: Literal["google_cloud", "openai", "hash"] = "google_cloud"
    rag_embedding_model: str = "gemini-embedding-001"
    rag_embedding_dimensions: int = 1536
    rag_top_k: int = 5
    rag_chunk_tokens: int = 1000
    rag_chunk_overlap_tokens: int = 180

    # Reasoning layer (explanation-only) LLM configuration.
    llm_reasoning_enabled: bool = True
    llm_provider: Literal[
        "openai",
        "ollama",
        "agent_platform_gemini",
        "agent_platform_claude",
        "vertex_gemini",
        "gcp_gemini",
        "vertex_claude",
        "gcp_claude",
    ] = "agent_platform_gemini"
    llm_model: str = "gemini-2.5-flash"
    llm_timeout_sec: float = 20.0
    llm_max_output_tokens: int = 2500
    openai_api_key: Optional[str] = None
    ollama_base_url: str = "http://localhost:11434/v1"
    gcp_project_id: Optional[str] = None
    gcp_location: str = ""
    gcp_genai_api_version: str = "v1"

    # Logging
    log_level: str = "INFO"

    @model_validator(mode="after")
    def reject_dev_product_adapters_in_prod(self) -> "Settings":
        if self.environment != "prod":
            return self
        unsafe = []
        if self.service_role in {"api", "ops", "ingest", "worker", "job"} and self.product_database_url.startswith("sqlite:"):
            unsafe.append("ARCHAGENT_PRODUCT_DATABASE_URL must use PostgreSQL")
        if self.service_role in {"api", "worker"} and not self.postgres_dsn:
            unsafe.append("ARCHAGENT_POSTGRES_DSN is unset")
        if self.service_role in {"api", "ops"} and self.auth_mode == "dev":
            unsafe.append("ARCHAGENT_AUTH_MODE=dev")
        if self.service_role in {"api", "ops", "worker"} and self.storage_backend == "filesystem":
            unsafe.append("ARCHAGENT_STORAGE_BACKEND=filesystem")
        if self.service_role in {"api", "ops", "ingest"} and self.task_dispatcher == "inline":
            unsafe.append("ARCHAGENT_TASK_DISPATCHER=inline")
        if self.service_role == "worker" and self.document_scan_mode == "dev_allow":
            unsafe.append("ARCHAGENT_DOCUMENT_SCAN_MODE=dev_allow")
        if self.service_role in {"api", "ops"} and not self.workos_cookie_password:
            unsafe.append("ARCHAGENT_WORKOS_COOKIE_PASSWORD is unset")
        if self.service_role in {"api", "ops", "worker"} and not self.workos_api_key:
            unsafe.append("ARCHAGENT_WORKOS_API_KEY is unset")
        if self.service_role in {"api", "ops", "worker"} and not self.workos_client_id:
            unsafe.append("ARCHAGENT_WORKOS_CLIENT_ID is unset")
        if self.service_role == "api" and not self.workos_webhook_secret:
            unsafe.append("ARCHAGENT_WORKOS_WEBHOOK_SECRET is unset")
        if self.service_role == "api" and not self.workos_webhook_path_token:
            unsafe.append("ARCHAGENT_WORKOS_WEBHOOK_PATH_TOKEN is unset")
        if self.service_role in {"api", "ops"} and not self.csrf_secret:
            unsafe.append("ARCHAGENT_CSRF_SECRET is unset")
        if self.allowed_origin_regex:
            unsafe.append("ARCHAGENT_ALLOWED_ORIGIN_REGEX must be unset")
        if self.service_role in {"api", "ops", "worker"} and self.storage_backend == "gcs" and not self.gcp_storage_bucket:
            unsafe.append("ARCHAGENT_GCP_STORAGE_BUCKET is unset")
        if self.service_role in {"api", "ops", "worker"} and self.storage_backend == "gcs" and not self.gcp_quarantine_bucket:
            unsafe.append("ARCHAGENT_GCP_QUARANTINE_BUCKET is unset")
        if self.task_dispatcher == "cloud_tasks" and not self.gcp_tasks_target_url:
            unsafe.append("ARCHAGENT_GCP_TASKS_TARGET_URL is unset")
        if self.task_dispatcher == "cloud_tasks" and not self.gcp_project_id:
            unsafe.append("ARCHAGENT_GCP_PROJECT_ID is unset")
        if self.task_dispatcher == "cloud_tasks" and not self.gcp_tasks_queue:
            unsafe.append("ARCHAGENT_GCP_TASKS_QUEUE is unset")
        if self.task_dispatcher == "cloud_tasks" and not self.gcp_tasks_service_account:
            unsafe.append("ARCHAGENT_GCP_TASKS_SERVICE_ACCOUNT is unset")
        if self.task_dispatcher == "cloud_tasks" and not self.gcp_tasks_oidc_audience:
            unsafe.append("ARCHAGENT_GCP_TASKS_OIDC_AUDIENCE is unset")
        if self.service_role == "worker" and not self.gcp_tasks_service_account:
            unsafe.append("ARCHAGENT_GCP_TASKS_SERVICE_ACCOUNT is unset")
        if self.service_role == "worker" and not self.gcp_tasks_oidc_audience:
            unsafe.append("ARCHAGENT_GCP_TASKS_OIDC_AUDIENCE is unset")
        if self.service_role == "worker" and not self.scanner_service_url:
            unsafe.append("ARCHAGENT_SCANNER_SERVICE_URL is unset")
        if self.service_role == "worker" and not self.scanner_service_token:
            unsafe.append("ARCHAGENT_SCANNER_SERVICE_TOKEN is unset")
        if unsafe:
            raise ValueError(f"Production cannot use local product adapters: {', '.join(unsafe)}")
        return self

    @property
    def allowed_origin_list(self) -> list[str]:
        return [item.strip() for item in self.allowed_origins.split(",") if item.strip()]

    @property
    def internal_user_id_set(self) -> set[str]:
        return {item.strip() for item in self.internal_user_ids.split(",") if item.strip()}


@lru_cache
def get_settings() -> Settings:
    """Singleton settings from env; call ``.cache_clear()`` before reload (see ``main``)."""
    return Settings()
