"""
Telemetry agent: canonicalize client-provided metrics and topology.

Downstream smell rules expect stable key names; ``_SIGNAL_ALIASES`` maps common
alternate names (e.g. Prometheus-style shortcuts) onto those keys.
"""

from __future__ import annotations

from typing import Any, Dict

from agent.app.logging_utils import get_logger
from agent.app.state import GraphState, ServiceTopology

logger = get_logger("agent.nodes.telemetry")

# Simple aliases to tolerate common metric naming conventions.
_SIGNAL_ALIASES: Dict[str, str] = {
    "latency_p95_ms": "request_latency_p95_ms",
    "latency_p99_ms": "request_latency_p99_ms",
    "request_p95_ms": "request_latency_p95_ms",
    "request_p99_ms": "request_latency_p99_ms",
    "db_p95_ms": "db_latency_p95_ms",
    "db_latency_ms": "db_latency_p95_ms",
    "errors": "error_rate",
    "cpu": "cpu_utilization",
    "mem": "memory_utilization",
    "memory": "memory_utilization",
    "backlog": "queue_backlog",
}


def normalize_signals(raw: Dict[str, float]) -> Dict[str, float]:
    """Apply ``_SIGNAL_ALIASES`` then coerce values to ``float`` (canonical keys for smell rules)."""
    normalized: Dict[str, float] = {}
    for k, v in raw.items():
        key = _SIGNAL_ALIASES.get(k, k)
        normalized[key] = float(v)
    return normalized


def telemetry_node(state: GraphState) -> GraphState:
    """
    Telemetry node: normalize raw input into canonical signals + topology.
    """

    raw_signals = state.get("raw_signals", state.get("signals", {}))
    raw_topology: Any = state.get("raw_topology", state.get("topology", {}))
    run_id = state.get("run_id", "n/a")
    logger.info(
        "telemetry_agent start run_id=%s raw_signal_keys=%s",
        run_id,
        sorted(raw_signals.keys()),
    )

    state["signals"] = normalize_signals(raw_signals)

    if isinstance(raw_topology, ServiceTopology):
        state["topology"] = raw_topology.model_dump(by_alias=True)
    else:
        state["topology"] = ServiceTopology.model_validate(raw_topology).model_dump(by_alias=True)
    logger.info(
        "telemetry_agent done run_id=%s normalized_signal_keys=%s services=%d edges=%d",
        run_id,
        sorted(state["signals"].keys()),
        len(state["topology"].get("services", [])),
        len(state["topology"].get("edges", [])),
    )
    return state

