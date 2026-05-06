"""
Shared types for the HTTP API and the LangGraph ``GraphState``.

Request/response models are Pydantic (validation + OpenAPI). ``GraphState`` is a
:class:`typing.TypedDict` so LangGraph can merge dict-shaped node returns without
a custom reducer (see README state model).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

from agent.app.models.pattern import ArchitecturePattern, Effort, Impact


class TelemetrySignals(BaseModel):
    """
    Normalized signals expected by downstream smell rules.

    The Telemetry node is responsible for mapping incoming raw metric names
    into these canonical fields.
    """

    request_latency_p95_ms: Optional[float] = None
    request_latency_p99_ms: Optional[float] = None
    db_latency_p95_ms: Optional[float] = None
    error_rate: Optional[float] = None  # 0..1
    cpu_utilization: Optional[float] = None  # 0..1
    memory_utilization: Optional[float] = None  # 0..1
    queue_backlog: Optional[float] = None
    saturation: Optional[float] = None  # generic 0..1, optional


class TopologyEdge(BaseModel):
    """One directed dependency between two services (API uses ``from`` / ``to`` aliases)."""

    model_config = ConfigDict(populate_by_name=True)

    from_service: str = Field(alias="from")
    to_service: str = Field(alias="to")
    type: str  # e.g. "http", "db", "queue"


class ServiceTopology(BaseModel):
    """Service list plus edges and optional critical infrastructure markers."""

    model_config = ConfigDict(populate_by_name=True)

    services: List[str] = Field(default_factory=list)
    edges: List[TopologyEdge] = Field(default_factory=list)
    critical_stores: List[str] = Field(default_factory=list)
    critical_queues: List[str] = Field(default_factory=list)


class Smell(BaseModel):
    """API projection of a smell dict from ``smell_rules.detect_smells``."""

    type: str
    severity: Impact = "medium"
    confidence: float = 0.8
    evidence: Dict[str, float | str] = Field(default_factory=dict)


class Recommendation(BaseModel):
    """One ranked architecture move proposed by the recommendation agent."""

    pattern: str
    solution: str
    impact: Impact
    effort: Effort
    priority: int = 99
    reason: str = ""


class Critique(BaseModel):
    """Risk or constraint warning from the critic agent for a given pattern."""

    pattern_id: str
    level: str  # "warning" | "blocker"
    message: str
    evidence: Dict[str, float | str] = Field(default_factory=dict)


class PlanStep(BaseModel):
    """Single ordered step in the planner output (1-based indexing in ``title`` for MVP)."""

    title: str
    description: str
    impact: Impact
    effort: Effort
    depends_on: List[int] = Field(default_factory=list)


class RecommendationRequest(BaseModel):
    """
    POST body: raw metric bag plus topology (telemetry node normalizes signal keys).

    When ``signals`` is empty and ``topology`` has no services or edges, the API
    loads the latest Kubernetes snapshot from Postgres (requires ``ARCHAGENT_POSTGRES_DSN``).
    """

    signals: Dict[str, float] = Field(default_factory=dict)
    topology: ServiceTopology = Field(default_factory=ServiceTopology)


def recommendation_request_has_inline_payload(req: RecommendationRequest) -> bool:
    """True when the client supplied explicit signals or topology to analyze."""
    if req.signals:
        return True
    return bool(req.topology.services or req.topology.edges)


class RecommendationResponse(BaseModel):
    """Full pipeline result returned to API clients."""

    smells: List[Smell]
    recommendations: List[Recommendation]
    critiques: List[Critique]
    plan: List[PlanStep]
    explanation_report: str = ""


class GraphState(TypedDict, total=False):
    """
    Working memory for all pipeline agents. Keys are populated progressively;
    ``total=False`` allows partial updates from each node.
    """

    run_id: str
    # Populated from the API body; consumed by telemetry_agent.
    raw_signals: Dict[str, float]
    raw_topology: Dict[str, Any]

    # Canonical inputs after telemetry_agent; then smells → … → explanation_report.
    signals: Dict[str, float]
    topology: Dict[str, Any]
    smells: List[Dict[str, Any]]
    patterns: List[ArchitecturePattern]
    recommendations: List[Recommendation]
    critiques: List[Critique]
    final_plan: List[PlanStep]
    explanation_report: str

