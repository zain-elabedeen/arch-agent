from __future__ import annotations

from typing import Dict

from agent.app.state import GraphState, ServiceTopology, TelemetrySignals


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


def normalize_signals(raw: Dict[str, float]) -> TelemetrySignals:
    normalized: Dict[str, float] = {}
    for k, v in raw.items():
        key = _SIGNAL_ALIASES.get(k, k)
        normalized[key] = v

    # Pydantic will ignore unknown fields by default? (In BaseModel v2 it errors
    # if extra is forbidden; we keep model default, so only set known attrs.)
    sig = TelemetrySignals()
    for field in sig.model_fields.keys():
        if field in normalized:
            setattr(sig, field, float(normalized[field]))
    return sig


def telemetry_node(state: GraphState) -> GraphState:
    """
    Telemetry node: normalize raw input into canonical signals + topology.
    """

    state.signals = normalize_signals(state.raw_signals)
    state.topology = ServiceTopology.model_validate(state.raw_topology.model_dump(by_alias=True))
    return state

