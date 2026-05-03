from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from agent.app.state import Smell, TelemetrySignals, TopologyEdge, ServiceTopology


@dataclass(frozen=True)
class SmellRule:
    smell_type: str
    detect: Callable[[TelemetrySignals, ServiceTopology], Optional[Smell]]


def _impact_from_ratio(r: float) -> str:
    if r >= 0.9:
        return "high"
    if r >= 0.75:
        return "medium"
    return "low"


def _confidence_from_evidence(keys_present: int) -> str:
    if keys_present >= 3:
        return "high"
    if keys_present == 2:
        return "medium"
    return "low"


def detect_cpu_saturation(signals: TelemetrySignals, topology: ServiceTopology) -> Optional[Smell]:
    if signals.cpu_utilization is None:
        return None
    if signals.cpu_utilization < 0.85:
        return None
    evidence: Dict[str, float | str] = {"cpu_utilization": signals.cpu_utilization}
    return Smell(
        type="cpu_saturation",
        severity=_impact_from_ratio(signals.cpu_utilization),
        confidence="medium",
        evidence=evidence,
    )


def detect_queue_backlog(signals: TelemetrySignals, topology: ServiceTopology) -> Optional[Smell]:
    if signals.queue_backlog is None:
        return None
    if signals.queue_backlog < 5000:
        return None
    has_queue_edge = any(e.type == "queue" for e in topology.edges)
    evidence: Dict[str, float | str] = {"queue_backlog": signals.queue_backlog}
    if has_queue_edge:
        evidence["topology"] = "queue_edge_present"
    return Smell(
        type="queue_backlog",
        severity="high" if signals.queue_backlog >= 20000 else "medium",
        confidence="high" if has_queue_edge else "medium",
        evidence=evidence,
    )


def detect_db_latency(signals: TelemetrySignals, topology: ServiceTopology) -> Optional[Smell]:
    if signals.db_latency_p95_ms is None:
        return None
    if signals.db_latency_p95_ms < 250:
        return None
    has_db_edge = any(e.type == "db" for e in topology.edges)
    evidence: Dict[str, float | str] = {"db_latency_p95_ms": signals.db_latency_p95_ms}
    if has_db_edge:
        evidence["topology"] = "db_edge_present"
    return Smell(
        type="db_latency_hotspot",
        severity="high" if signals.db_latency_p95_ms >= 600 else "medium",
        confidence="high" if has_db_edge else "medium",
        evidence=evidence,
    )


def detect_request_latency(signals: TelemetrySignals, topology: ServiceTopology) -> Optional[Smell]:
    p95 = signals.request_latency_p95_ms
    if p95 is None:
        return None
    if p95 < 600:
        return None
    evidence: Dict[str, float | str] = {"request_latency_p95_ms": p95}
    keys_present = 1 + int(signals.db_latency_p95_ms is not None) + int(signals.cpu_utilization is not None)
    return Smell(
        type="request_latency_regression",
        severity="high" if p95 >= 1200 else "medium",
        confidence=_confidence_from_evidence(keys_present),
        evidence=evidence,
    )


def detect_coupling_risk(signals: TelemetrySignals, topology: ServiceTopology) -> Optional[Smell]:
    """
    A simple topology-only smell: many inbound edges into a single service can
    indicate architectural coupling and change-risk concentration.
    """

    inbound: Dict[str, int] = {s: 0 for s in topology.services}
    for e in topology.edges:
        if e.to_service in inbound:
            inbound[e.to_service] += 1
        else:
            inbound[e.to_service] = 1
    if not inbound:
        return None

    hotspot, degree = max(inbound.items(), key=lambda kv: kv[1])
    if degree < 4:
        return None
    evidence: Dict[str, float | str] = {"service": hotspot, "inbound_dependencies": float(degree)}
    return Smell(type="coupling_risk", severity="medium" if degree < 7 else "high", confidence="medium", evidence=evidence)


DEFAULT_RULES: List[SmellRule] = [
    SmellRule("cpu_saturation", detect_cpu_saturation),
    SmellRule("queue_backlog", detect_queue_backlog),
    SmellRule("db_latency_hotspot", detect_db_latency),
    SmellRule("request_latency_regression", detect_request_latency),
    SmellRule("coupling_risk", detect_coupling_risk),
]


def run_smell_rules(signals: TelemetrySignals, topology: ServiceTopology, rules: List[SmellRule] | None = None) -> List[Smell]:
    rules = rules or DEFAULT_RULES
    smells: List[Smell] = []
    for rule in rules:
        smell = rule.detect(signals, topology)
        if smell is not None:
            smells.append(smell)
    return smells

