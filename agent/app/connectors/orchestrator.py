"""
Ingestion orchestrator worker.

This worker owns run creation. It creates one ``runs.id`` per ingestion cycle,
then calls each configured connector worker with that shared ``run_id`` so every
connector contributes to the same snapshot.
"""

from __future__ import annotations

import sys
import time
import uuid
from typing import Iterable, List

from sqlalchemy import create_engine

from agent.app.config import Settings, get_settings
from agent.app.connectors.kubernetes.client import build_apis
from agent.app.connectors.kubernetes.worker import run_ingestion_once as run_kubernetes_ingestion_once
from agent.app.connectors.logs.worker import run_ingestion_once as run_logs_ingestion_once
from agent.app.connectors.repository import create_run, delete_run, ensure_connector_schema
from agent.app.logging_utils import configure_logging, get_logger

logger = get_logger("agent.connectors.orchestrator")

_KUBERNETES_API_CONNECTORS = {"kubernetes", "logs"}


def _connector_names(settings: Settings) -> List[str]:
    names = [item.strip().lower() for item in (settings.ingestion_connectors or "").split(",") if item.strip()]
    if not names:
        names = ["kubernetes"]
    if not settings.logs_enabled:
        names = [name for name in names if name != "logs"]
    return names


def _needs_kubernetes_api(connectors: Iterable[str]) -> bool:
    return any(name in _KUBERNETES_API_CONNECTORS for name in connectors)


def _run_connector(name: str, run_id: uuid.UUID, apis=None) -> None:
    if name == "kubernetes":
        run_kubernetes_ingestion_once(run_id=run_id, apis=apis)
        return
    if name == "logs":
        run_logs_ingestion_once(run_id=run_id, apis=apis)
        return
    raise ValueError(f"Unsupported ingestion connector: {name}")


def run_orchestration_once() -> uuid.UUID:
    settings = get_settings()
    if not settings.postgres_dsn:
        raise RuntimeError("ARCHAGENT_POSTGRES_DSN is required for the ingestion orchestrator.")

    connectors = _connector_names(settings)
    if not connectors:
        raise RuntimeError("No ingestion connectors are enabled.")

    engine = create_engine(settings.postgres_dsn, pool_pre_ping=True)
    if settings.k8s_auto_migrate:
        ensure_connector_schema(engine)

    with engine.begin() as conn:
        run_id = create_run(conn)

    try:
        apis = build_apis() if _needs_kubernetes_api(connectors) else None
    except Exception:
        with engine.begin() as conn:
            delete_run(conn, run_id)
        raise

    successes: List[str] = []
    failures: List[str] = []
    for connector in connectors:
        try:
            _run_connector(connector, run_id, apis=apis)
            successes.append(connector)
        except Exception as e:
            failures.append(connector)
            logger.exception(
                "connector ingestion failed run_id=%s connector=%s error=%s",
                run_id,
                connector,
                e,
            )

    if not successes:
        with engine.begin() as conn:
            delete_run(conn, run_id)
        raise RuntimeError(f"all ingestion connectors failed: {', '.join(failures)}")

    logger.info(
        "orchestrated ingestion saved run_id=%s successes=%s failures=%s",
        run_id,
        successes,
        failures,
    )
    return run_id


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    interval = max(15, int(settings.k8s_poll_interval_sec))
    logger.info(
        "ingestion orchestrator starting interval_sec=%s connectors=%s",
        interval,
        _connector_names(settings),
    )
    consecutive_failures = 0
    while True:
        try:
            run_orchestration_once()
            consecutive_failures = 0
        except Exception as e:
            consecutive_failures += 1
            logger.error("orchestrated ingestion failed failures=%d error=%s", consecutive_failures, e)
            if consecutive_failures == 1 or consecutive_failures % 5 == 0:
                logger.exception("orchestrated ingestion traceback failures=%d", consecutive_failures)
        time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
