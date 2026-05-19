"""
Application settings (``ARCHAGENT_*`` env vars).

Environment-specific defaults and optional LLM keys for the reasoning agent live
here; ``main.recommend`` clears the settings cache and passes fresh ``Settings``
into ``build_graph`` each request.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal, Optional

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
    rag_embedding_provider: Literal["openai", "hash"] = "openai"
    rag_embedding_model: str = "text-embedding-3-small"
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


@lru_cache
def get_settings() -> Settings:
    """Singleton settings from env; call ``.cache_clear()`` before reload (see ``main``)."""
    return Settings()
