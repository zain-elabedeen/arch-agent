"""Outbound-only Kubernetes collector loop for customer clusters."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from agent.app.config import get_settings
from agent.app.connectors.hosted_client import HostedCollectorClient
from agent.app.connectors.kubernetes.worker import _collect_and_normalize_kubernetes
from agent.app.logging_utils import configure_logging, get_logger

logger = get_logger("agent.connectors.hosted")


def run_once() -> None:
    settings = get_settings()
    client = HostedCollectorClient(settings)
    client.register_if_needed()
    snapshot = _collect_and_normalize_kubernetes(settings)
    client.enqueue_snapshot(snapshot)
    client.flush_snapshots()
    last_successful_upload_at = datetime.now(timezone.utc).isoformat()
    namespaces = sorted({str(item.get("namespace")) for item in snapshot.get("services") or [] if item.get("namespace")})
    client.heartbeat(
        {
            "version": "0.1.0",
            "last_successful_upload_at": last_successful_upload_at,
            "permissions": ["pods:list", "services:list", "deployments:list", "horizontalpodautoscalers:list"],
            "namespaces": namespaces,
            "modules": {"kubernetes": "healthy", "logs": "enabled" if settings.logs_enabled else "disabled"},
        }
    )


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    interval = max(15, int(settings.k8s_poll_interval_sec))
    while True:
        try:
            run_once()
        except Exception:
            logger.exception("hosted collector cycle failed")
        time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
