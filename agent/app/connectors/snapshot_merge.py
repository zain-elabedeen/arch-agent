"""Helpers for merging connector outputs into one canonical run snapshot."""

from __future__ import annotations

from typing import Any, Dict


def _numeric_signals(signals: Dict[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for key, value in signals.items():
        if isinstance(value, (int, float, bool)):
            out[key] = float(value)
    return out


def snapshot_with_logs(base_snapshot: Dict[str, Any] | None, logs: Dict[str, Any]) -> Dict[str, Any]:
    """Merge normalized log evidence into an existing snapshot, or create a logs-only snapshot."""
    snapshot: Dict[str, Any] = dict(base_snapshot or {})
    snapshot["logs"] = logs

    signals = dict(snapshot.get("signals") or {})
    signals.update(_numeric_signals(logs.get("signals") or {}))
    snapshot["signals"] = signals

    topology = dict(snapshot.get("topology") or {})
    service_details = dict(topology.get("service_details") or {})
    services = set(topology.get("services") or [])
    for service, service_log_signals in (logs.get("service_signals") or {}).items():
        services.add(service)
        detail = dict(service_details.get(service) or {})
        detail["log_summary"] = service_log_signals
        service_details[service] = detail
    topology["services"] = sorted(services)
    topology["edges"] = topology.get("edges") or []
    topology["service_details"] = service_details
    snapshot["topology"] = topology

    if not snapshot.get("services"):
        snapshot["services"] = [
            {
                "name": service,
                "namespace": (service_details.get(service) or {}).get("namespace"),
                "cpu": 0.0,
                "memory": 0.0,
                "replicas": 0,
                "available_replicas": None,
                "unavailable_replicas": None,
                "restarts": 0,
            }
            for service in sorted(services)
        ]
    return snapshot


def snapshot_with_kubernetes(
    base_snapshot: Dict[str, Any] | None,
    kubernetes_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge Kubernetes metrics/topology while preserving any logs already attached to the run."""
    snapshot = dict(kubernetes_snapshot)
    logs = (base_snapshot or {}).get("logs")
    if isinstance(logs, dict) and logs:
        return snapshot_with_logs(snapshot, logs)
    return snapshot

