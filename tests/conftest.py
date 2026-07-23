import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolate_product_adapter_settings(monkeypatch, tmp_path):
    """Keep local tests independent from developer or deployment .env files."""
    from agent.app.config import get_settings
    from agent.app.product.store import get_product_store

    monkeypatch.setenv("ARCHAGENT_ENVIRONMENT", "test")
    monkeypatch.setenv("ARCHAGENT_AUTH_MODE", "dev")
    monkeypatch.setenv("ARCHAGENT_PRODUCT_DATABASE_URL", f"sqlite:///{tmp_path / 'product.db'}")
    monkeypatch.setenv("ARCHAGENT_STORAGE_BACKEND", "filesystem")
    monkeypatch.setenv("ARCHAGENT_TASK_DISPATCHER", "inline")
    monkeypatch.setenv("ARCHAGENT_DOCUMENT_SCAN_MODE", "dev_allow")
    monkeypatch.setenv("ARCHAGENT_COLLECTOR_REGISTRATION_ENDPOINT", "https://api.example.invalid")
    monkeypatch.setenv("ARCHAGENT_ALLOWED_ORIGIN_REGEX", "")
    get_settings.cache_clear()
    get_product_store.cache_clear()
    yield
    get_settings.cache_clear()
    get_product_store.cache_clear()
