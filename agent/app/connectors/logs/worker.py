"""
Logs ingestion loop.

Runs independently from the Kubernetes metrics/topology worker. The first log
source is Kubernetes pod logs, but collection returns source-neutral batches so
future sources can plug into the same normalization and persistence path.
"""

from __future__ import annotations

import socket
import sys
import time
import uuid
from typing import Dict
from urllib.parse import urlparse

from kubernetes.client import ApiException
from sqlalchemy import create_engine
from urllib3.exceptions import MaxRetryError, NewConnectionError

from agent.app.config import Settings, get_settings
from agent.app.connectors.kubernetes.client import K8sApis, build_apis
from agent.app.connectors.repository import (
    ensure_connector_schema,
    load_latest_snapshot,
    load_run_snapshot,
    replace_run_snapshot,
    save_run,
)
from agent.app.connectors.logs.kubernetes_source import collect_kubernetes_logs
from agent.app.connectors.logs.normalizer import normalize_logs
from agent.app.connectors.snapshot_merge import snapshot_with_logs
from agent.app.logging_utils import configure_logging, get_logger

logger = get_logger("agent.connectors.logs.worker")


def _namespace_csv(value: str) -> set[str]:
    return {item.strip() for item in (value or "").split(",") if item.strip()}


def _collect_and_normalize_logs(settings: Settings, apis: K8sApis | None = None) -> Dict[str, Any]:
    apis = apis or build_apis()
    pods = apis.core.list_pod_for_all_namespaces(watch=False).items
    raw_logs = collect_kubernetes_logs(
        apis,
        pods,
        include_namespaces=_namespace_csv(settings.k8s_include_namespaces),
        exclude_namespaces=_namespace_csv(settings.k8s_exclude_namespaces),
        since_seconds=int(settings.k8s_poll_interval_sec) + int(settings.log_window_grace_sec),
        tail_lines=int(settings.log_tail_lines),
    )
    logs = normalize_logs(
        raw_logs,
        logs_enabled=bool(settings.logs_enabled),
    )
    return logs


def run_ingestion_once(run_id: uuid.UUID | None = None, apis: K8sApis | None = None) -> uuid.UUID | None:
    settings = get_settings()
    if not settings.postgres_dsn:
        raise RuntimeError("ARCHAGENT_POSTGRES_DSN is required for the logs worker.")
    if not settings.logs_enabled:
        logger.info("logs worker skipped because ARCHAGENT_LOGS_ENABLED=false")
        return run_id

    engine = create_engine(settings.postgres_dsn, pool_pre_ping=True)
    if settings.k8s_auto_migrate:
        ensure_connector_schema(engine)

    logs = _collect_and_normalize_logs(settings, apis)

    with engine.begin() as conn:
        if run_id:
            snapshot = load_run_snapshot(conn, run_id)
            if snapshot is None:
                raise LookupError("run_not_found")
            rid = run_id
            replace_run_snapshot(conn, rid, snapshot_with_logs(snapshot, logs))
        else:
            rid = save_run(conn, snapshot_with_logs(None, logs))
    logger.info(
        "logs snapshot saved run_id=%s events=%d services=%d",
        rid,
        len(logs.get("events") or []),
        len((logs.get("service_signals") or {}).keys()),
    )
    return rid


def _is_connection_refused_error(exc: BaseException) -> bool:
    cur: BaseException | None = exc
    while cur is not None:
        if isinstance(cur, (ConnectionRefusedError, socket.gaierror, TimeoutError, NewConnectionError, MaxRetryError)):
            return True
        cur = cur.__cause__  # type: ignore[assignment]
    return False


def _failure_hint(exc: Exception, k8s_host: str) -> str:
    parsed = urlparse(k8s_host or "")
    host = parsed.hostname or "unknown"
    if isinstance(exc, ApiException):
        if exc.status in (401, 403):
            return "Kubernetes auth/RBAC denied. Verify pod/log list permissions."
        if exc.status == 404:
            return "Kubernetes pod log endpoint not found. Verify cluster API server status."
    if _is_connection_refused_error(exc):
        if host in {"127.0.0.1", "localhost"}:
            return "Kubernetes API host resolves to loopback from inside the container."
        return "Network connection to Kubernetes API failed."
    return "Unexpected logs ingestion failure. Review traceback and kubeconfig context."


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    interval = max(15, int(settings.k8s_poll_interval_sec))
    logger.info("logs worker starting interval_sec=%s source=kubernetes", interval)
    consecutive_failures = 0
    last_k8s_host = "n/a"
    while True:
        try:
            apis = build_apis()
            last_k8s_host = apis.host or last_k8s_host
            run_ingestion_once(apis=apis)
            consecutive_failures = 0
        except Exception as e:
            consecutive_failures += 1
            logger.error(
                "logs ingestion failed failures=%d k8s_host=%s hint=%s error=%s",
                consecutive_failures,
                last_k8s_host,
                _failure_hint(e, last_k8s_host),
                e,
            )
            if consecutive_failures == 1 or consecutive_failures % 5 == 0:
                logger.exception("logs ingestion traceback failures=%d", consecutive_failures)
        time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
