"""
Deterministic architecture **smell** detection.

Smells are not root-cause diagnoses; they are stable labels that downstream agents
(retrieval, recommend, critic) use to ground recommendations. All thresholds and
topology heuristics live here so behavior stays testable and explainable.
"""

from __future__ import annotations

from typing import Dict, List


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
    backlog = _value(metrics, "backlog", "queue_backlog")
    error_rate = _value(metrics, "error_rate")

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
                "evidence": {"cpu": cpu},
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

