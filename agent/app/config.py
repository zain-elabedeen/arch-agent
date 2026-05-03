from __future__ import annotations

from functools import lru_cache
from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Configuration is intentionally small for the MVP.

    We design for PostgreSQL, but allow a filesystem/in-memory fallback so the
    system is runnable in early prototyping environments.
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

    # Pattern catalog location for filesystem mode
    patterns_path: str = "agent/app/patterns"

    # Reasoning layer (explanation-only) LLM configuration.
    llm_reasoning_enabled: bool = True
    llm_provider: Literal["openai", "ollama"] = "openai"
    llm_model: str = "gpt-4o-mini"
    openai_api_key: Optional[str] = None
    ollama_base_url: str = "http://localhost:11434/v1"

    # Logging
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()

