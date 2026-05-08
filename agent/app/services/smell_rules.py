"""
Deterministic architecture **smell** detection.

Smells are not root-cause diagnoses; they are stable labels that downstream agents
(retrieval, recommend, critic) use to ground recommendations. All thresholds and
topology heuristics live here so behavior stays testable and explainable.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _value(metrics: Dict[str, float], *keys: str) -> float | None:
    """First present key wins (supports canonical and legacy metric names)."""
    for key in keys:
        v = metrics.get(key)
        if v is not None:
            return float(v)
    return None


def _severity_for_threshold(value: float, warn: float, high: float) -> str:
    """Bucket a scalar into smell severity labels for threshold-style rules."""
    return "high" if value >= high else ("medium" if value >= warn else "low")


def _confidence_for_coupling(deps: int) -> float:
    """Higher outbound dependency count ⇒ slightly higher confidence in coupling smell."""
    if deps > 6:
        return 0.92
    if deps > 4:
        return 0.86
    return 0.8


def _service_details(topology: dict) -> Dict[str, Dict[str, Any]]:
    details = topology.get("service_details", {}) if isinstance(topology, dict) else {}
    return details if isinstance(details, dict) else {}


def _join_services(names: List[str]) -> str:
    return ", ".join(sorted(names))


def _services_matching(topology: dict, predicate) -> List[str]:
    matches: List[str] = []
    for name, detail in _service_details(topology).items():
        if isinstance(detail, dict) and predicate(detail):
            matches.append(str(name))
    return sorted(matches)


def _services_matching_log_summary(topology: dict, key: str, threshold: float) -> List[str]:
    """Services whose per-service log summary has ``key`` above ``threshold``."""
    matches: List[str] = []
    for name, detail in _service_details(topology).items():
        if not isinstance(detail, dict):
            continue
        summary = detail.get("log_summary") or {}
        if not isinstance(summary, dict):
            continue
        try:
            value = float(summary.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
        if value > threshold:
            matches.append(str(name))
    return sorted(matches)


def detect_smells(metrics: dict, topology: dict) -> list[dict]:
    """
    Deterministic smell detection from canonical signals + topology.
    Returns stable dict objects suitable for explainable downstream use.
    """
    smells: List[dict] = []

    # --- Metric-backed smells (thresholds are MVP constants; tune with product input) ---
    db_latency = _value(metrics, "db_latency_ms", "db_latency_p95_ms")
    req_p95 = _value(metrics, "request_latency_p95_ms")
    cpu = _value(metrics, "cpu", "cpu_utilization")
    memory = _value(metrics, "memory", "memory_utilization")
    backlog = _value(metrics, "backlog", "queue_backlog")
    error_rate = _value(metrics, "error_rate")
    status_5xx_rate = _value(metrics, "status_5xx_rate")
    status_4xx_rate = _value(metrics, "status_4xx_rate")
    request_count = _value(metrics, "request_count")
    timeout_count = _value(metrics, "timeout_count")
    dependency_error_count = _value(metrics, "dependency_error_count")
    probe_failure_count = _value(metrics, "probe_failure_count")
    oom_killed_count = _value(metrics, "oom_killed_count")
    crash_signal_count = _value(metrics, "crash_signal_count")
    restarts = _value(metrics, "pod_restart_total", "restart_count")
    unavailable = _value(metrics, "unavailable_replicas")
    single_instance_services = _value(metrics, "single_instance_service_count")
    hpa_pressure = _value(metrics, "hpa_scaling_pressure")
    cpu_services = _services_matching(topology, lambda d: float(d.get("cpu") or 0.0) > 0.9)
    memory_services = _services_matching(topology, lambda d: float(d.get("memory") or 0.0) > 0.9)
    restart_services = _services_matching(topology, lambda d: int(d.get("restarts") or 0) >= 3)
    unavailable_services = _services_matching(topology, lambda d: int(d.get("unavailable_replicas") or 0) > 0)
    single_instance_service_names = _services_matching(topology, lambda d: int(d.get("replicas") or 0) <= 1)
    error_services = _services_matching_log_summary(topology, "error_rate", 0.05)
    status_5xx_services = _services_matching_log_summary(topology, "status_5xx_rate", 0.03)
    timeout_services = _services_matching_log_summary(topology, "timeout_count", 0.0)
    dependency_error_services = _services_matching_log_summary(topology, "dependency_error_count", 0.0)
    probe_failure_services = _services_matching_log_summary(topology, "probe_failure_count", 0.0)
    crash_signal_services = _services_matching_log_summary(topology, "crash_signal_count", 0.0)

    if db_latency is not None and req_p95 is not None and db_latency > 250 and req_p95 > 500:
        smells.append(
            {
                "type": "read_scaling_bottleneck",
                "severity": "high" if db_latency > 500 or req_p95 > 900 else "medium",
                "confidence": 0.9,
                "evidence": {"db_latency_ms": db_latency, "request_latency_p95_ms": req_p95},
            }
        )

    if cpu is not None and cpu > 0.9:
        smells.append(
            {
                "type": "cpu_saturation",
                "severity": _severity_for_threshold(cpu, warn=0.9, high=0.97),
                "confidence": 0.88,
                "evidence": {"cpu": cpu, **({"services": _join_services(cpu_services)} if cpu_services else {})},
            }
        )

    if memory is not None and memory > 0.9:
        smells.append(
            {
                "type": "memory_pressure",
                "severity": _severity_for_threshold(memory, warn=0.9, high=0.97),
                "confidence": 0.84,
                "evidence": {"memory": memory, **({"services": _join_services(memory_services)} if memory_services else {})},
            }
        )

    if backlog is not None and backlog > 10000:
        smells.append(
            {
                "type": "queue_backlog",
                "severity": "high" if backlog > 25000 else "medium",
                "confidence": 0.87,
                "evidence": {"backlog": backlog},
            }
        )

    if (error_rate is not None and error_rate > 0.05) or (status_5xx_rate is not None and status_5xx_rate > 0.03):
        evidence = {
            **({"error_rate": error_rate} if error_rate is not None else {}),
            **({"status_5xx_rate": status_5xx_rate} if status_5xx_rate is not None else {}),
            **({"status_4xx_rate": status_4xx_rate} if status_4xx_rate is not None else {}),
            **({"request_count": request_count} if request_count is not None else {}),
        }
        services = sorted(set(error_services + status_5xx_services))
        smells.append(
            {
                "type": "error_burst",
                "severity": "high" if (error_rate or 0.0) > 0.12 or (status_5xx_rate or 0.0) > 0.08 else "medium",
                "confidence": 0.82,
                "evidence": {**evidence, **({"services": _join_services(services)} if services else {})},
            }
        )

    if timeout_count is not None and timeout_count >= 3:
        smells.append(
            {
                "type": "timeout_pressure",
                "severity": "high" if timeout_count >= 10 else "medium",
                "confidence": 0.82,
                "evidence": {
                    "timeout_count": timeout_count,
                    **({"request_latency_p95_ms": req_p95} if req_p95 is not None else {}),
                    **({"services": _join_services(timeout_services)} if timeout_services else {}),
                },
            }
        )

    if dependency_error_count is not None and dependency_error_count >= 2:
        smells.append(
            {
                "type": "dependency_instability",
                "severity": "high" if dependency_error_count >= 8 else "medium",
                "confidence": 0.8,
                "evidence": {
                    "dependency_error_count": dependency_error_count,
                    **({"services": _join_services(dependency_error_services)} if dependency_error_services else {}),
                },
            }
        )

    if probe_failure_count is not None and probe_failure_count > 0:
        smells.append(
            {
                "type": "probe_instability",
                "severity": "high" if probe_failure_count >= 3 else "medium",
                "confidence": 0.76,
                "evidence": {
                    "probe_failure_count": probe_failure_count,
                    **({"services": _join_services(probe_failure_services)} if probe_failure_services else {}),
                },
            }
        )

    if (crash_signal_count is not None and crash_signal_count > 0) or (oom_killed_count is not None and oom_killed_count > 0):
        smells.append(
            {
                "type": "crash_loop_signal",
                "severity": "high" if (crash_signal_count or 0.0) >= 3 or (oom_killed_count or 0.0) >= 1 else "medium",
                "confidence": 0.8,
                "evidence": {
                    **({"crash_signal_count": crash_signal_count} if crash_signal_count is not None else {}),
                    **({"oom_killed_count": oom_killed_count} if oom_killed_count is not None else {}),
                    **({"services": _join_services(crash_signal_services)} if crash_signal_services else {}),
                },
            }
        )

    if restarts is not None and restarts >= 3:
        smells.append(
            {
                "type": "restart_instability",
                "severity": "high" if restarts >= 10 else "medium",
                "confidence": 0.78,
                "evidence": {
                    "pod_restart_total": restarts,
                    **({"services": _join_services(restart_services)} if restart_services else {}),
                },
            }
        )

    if unavailable is not None and unavailable > 0:
        smells.append(
            {
                "type": "replica_unavailability",
                "severity": "high" if unavailable >= 3 else "medium",
                "confidence": 0.82,
                "evidence": {
                    "unavailable_replicas": unavailable,
                    **({"services": _join_services(unavailable_services)} if unavailable_services else {}),
                },
            }
        )

    if hpa_pressure is not None and hpa_pressure > 1.0:
        smells.append(
            {
                "type": "autoscaling_pressure",
                "severity": "high" if hpa_pressure >= 1.5 else "medium",
                "confidence": 0.8,
                "evidence": {"hpa_scaling_pressure": hpa_pressure},
            }
        )

    # --- Topology-backed smell: many outbound deps from one service ---
    edges = topology.get("edges", []) if isinstance(topology, dict) else []
    outbound_deps: Dict[str, int] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        from_service = edge.get("from") or edge.get("from_service")
        to_service = edge.get("to") or edge.get("to_service")
        if not from_service or not to_service:
            continue
        outbound_deps[from_service] = outbound_deps.get(from_service, 0) + 1
    for service, dep_count in outbound_deps.items():
        if dep_count > 3:
            smells.append(
                {
                    "type": "coupling_risk",
                    "severity": "high" if dep_count > 6 else "medium",
                    "confidence": _confidence_for_coupling(dep_count),
                    "evidence": {"service": service, "dependencies": float(dep_count)},
                }
            )

    if single_instance_services is None and single_instance_service_names:
        single_instance_services = float(len(single_instance_service_names))
    if single_instance_services is not None and single_instance_services > 0:
        smells.append(
            {
                "type": "single_instance_risk",
                "severity": "medium",
                "confidence": 0.74,
                "evidence": {
                    "single_instance_service_count": single_instance_services,
                    **({"services": _join_services(single_instance_service_names)} if single_instance_service_names else {}),
                },
            }
        )

    if error_rate is not None and error_rate > 0.05:
        smells.append(
            {
                "type": "high_error_rate",
                "severity": "high" if error_rate > 0.12 else "medium",
                "confidence": 0.85,
                "evidence": {"error_rate": error_rate},
            }
        )

    return smells
