"""
Ingestion loop: collect → normalize → persist, on a fixed interval.

Run as ``python -m agent.app.connectors.kubernetes.worker`` with
``ARCHAGENT_POSTGRES_DSN`` and valid kube credentials.
"""

from __future__ import annotations

import socket
import sys
import time
import uuid
from urllib.parse import urlparse

from kubernetes.client import ApiException
from sqlalchemy import create_engine
from urllib3.exceptions import MaxRetryError, NewConnectionError

from agent.app.config import get_settings
from agent.app.connectors.kubernetes.client import build_apis
from agent.app.connectors.kubernetes.collector import collect
from agent.app.connectors.kubernetes.normalizer import normalize
from agent.app.connectors.repository import ensure_connector_schema, load_run_snapshot, replace_run_snapshot, save_run
from agent.app.connectors.snapshot_merge import snapshot_with_kubernetes
from agent.app.logging_utils import configure_logging, get_logger

logger = get_logger("agent.connectors.k8s.worker")


def _namespace_csv(value: str) -> set[str]:
    """Parse comma-separated namespace env settings into a clean set."""
    return {item.strip() for item in (value or "").split(",") if item.strip()}


def _normalize_collected(c, settings):
    return normalize(
        c.pods,
        c.deployments,
        c.services,
        c.pod_metrics,
        c.hpas,
        config_maps=c.config_maps,
        include_namespaces=_namespace_csv(settings.k8s_include_namespaces),
        exclude_namespaces=_namespace_csv(settings.k8s_exclude_namespaces),
    )


def _collect_and_normalize_kubernetes(settings, apis=None):
    apis = apis or build_apis()
    c = collect(apis)
    return _normalize_collected(c, settings)


def run_ingestion_once(run_id: uuid.UUID | None = None, apis=None) -> uuid.UUID:
    settings = get_settings()
    if not settings.postgres_dsn:
        raise RuntimeError("ARCHAGENT_POSTGRES_DSN is required for the Kubernetes worker.")
    
    engine = create_engine(settings.postgres_dsn, pool_pre_ping=True)
    if settings.k8s_auto_migrate:
        ensure_connector_schema(engine)

    normalized = _collect_and_normalize_kubernetes(settings, apis)

    with engine.begin() as conn:
        if run_id:
            existing = load_run_snapshot(conn, run_id)
            if existing is None:
                raise LookupError("run_not_found")
            rid = run_id
            replace_run_snapshot(conn, rid, snapshot_with_kubernetes(existing, normalized))
        else:
            rid = save_run(conn, normalized)
    logger.info("k8s snapshot saved run_id=%s services=%d", rid, len(normalized.get("services") or []))
    return rid


def _is_connection_refused_error(exc: BaseException) -> bool:
    cur: BaseException | None = exc
    while cur is not None:
        if isinstance(cur, (ConnectionRefusedError, socket.gaierror, TimeoutError, NewConnectionError, MaxRetryError)):
            return True
        cur = cur.__cause__  # type: ignore[assignment]
    return False


def _ingestion_failure_hint(exc: Exception, k8s_host: str) -> str:
    parsed = urlparse(k8s_host or "")
    host = parsed.hostname or "unknown"
    if isinstance(exc, ApiException):
        if exc.status in (401, 403):
            return "Kubernetes auth/RBAC denied. Verify kubeconfig credentials and list permissions."
        if exc.status == 404:
            return "Kubernetes endpoint/resource not found. Verify cluster API server and metrics-server installation."
        if exc.status >= 500:
            return "Kubernetes API server is unhealthy/unreachable. Check cluster control plane status."
    if _is_connection_refused_error(exc):
        if host in {"127.0.0.1", "localhost"}:
            return (
                "Kubernetes API host resolves to loopback from inside the container. "
                "Use kubeconfig with reachable cluster host/IP (not localhost) or run worker outside container."
            )
        return "Network connection to Kubernetes API failed. Verify container network routing and endpoint reachability."
    return "Unexpected ingestion failure. Review traceback and kubeconfig context."


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    interval = max(15, int(settings.k8s_poll_interval_sec))
    logger.info("kubernetes worker starting interval_sec=%s", interval)
    consecutive_failures = 0
    last_k8s_host = "n/a"
    while True:
        try:
            apis = build_apis()
            last_k8s_host = apis.host or last_k8s_host
            settings = get_settings()
            if not settings.postgres_dsn:
                raise RuntimeError("ARCHAGENT_POSTGRES_DSN is required for the Kubernetes worker.")
            engine = create_engine(settings.postgres_dsn, pool_pre_ping=True)
            if settings.k8s_auto_migrate:
                ensure_connector_schema(engine)
            normalized = _collect_and_normalize_kubernetes(settings, apis)
            with engine.begin() as conn:
                rid = save_run(conn, normalized)
            logger.info(
                "k8s snapshot saved run_id=%s services=%d k8s_host=%s",
                rid,
                len(normalized.get("services") or []),
                last_k8s_host,
            )
            consecutive_failures = 0
        except Exception as e:
            consecutive_failures += 1
            hint = _ingestion_failure_hint(e, last_k8s_host)
            logger.error(
                "k8s ingestion failed failures=%d k8s_host=%s hint=%s error=%s",
                consecutive_failures,
                last_k8s_host,
                hint,
                e,
            )
            if consecutive_failures == 1 or consecutive_failures % 5 == 0:
                logger.exception("k8s ingestion traceback failures=%d", consecutive_failures)
        time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
