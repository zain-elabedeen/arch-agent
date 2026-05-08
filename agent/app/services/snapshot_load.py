"""
Load canonical ``raw_signals`` / ``raw_topology`` from the latest connector snapshot.

Used by ``POST /v1/recommendations`` when the request body carries no inline payload.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from sqlalchemy import create_engine

from agent.app.config import Settings
from agent.app.connectors.repository import ensure_connector_schema, load_run_as_raw_state


@lru_cache(maxsize=8)
def _engine_for(dsn: str):
    return create_engine(dsn, pool_pre_ping=True)


def fetch_snapshot_raw(
    settings: Settings,
    run_id: Optional[UUID],
) -> Tuple[dict, dict, Dict[str, Any], UUID]:
    """
    Return ``(raw_signals, raw_topology, raw_logs, db_run_id)`` from Postgres.

    Raises:
        RuntimeError: if ``postgres_dsn`` is not configured.
        LookupError: pass-through from ``load_run_as_raw_state`` (``no_snapshot``, etc.).
    """
    if not settings.postgres_dsn:
        raise RuntimeError("ARCHAGENT_POSTGRES_DSN is not set")
    eng = _engine_for(settings.postgres_dsn)
    if settings.k8s_auto_migrate:
        ensure_connector_schema(eng)
    with eng.connect() as conn:
        return load_run_as_raw_state(conn, run_id)
