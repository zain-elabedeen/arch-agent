from __future__ import annotations

from typing import Any, Dict

from agent.app.state import GraphState, ServiceTopology


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

    state["signals"] = normalize_signals(raw_signals)

    if isinstance(raw_topology, ServiceTopology):
        state["topology"] = raw_topology.model_dump(by_alias=True)
    else:
        state["topology"] = ServiceTopology.model_validate(raw_topology).model_dump(by_alias=True)
    return state

