from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

from agent.app.models.pattern import ArchitecturePattern, Confidence, Effort, Impact


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
    model_config = ConfigDict(populate_by_name=True)

    from_service: str = Field(alias="from")
    to_service: str = Field(alias="to")
    type: str  # e.g. "http", "db", "queue"


class ServiceTopology(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    services: List[str] = Field(default_factory=list)
    edges: List[TopologyEdge] = Field(default_factory=list)
    critical_stores: List[str] = Field(default_factory=list)
    critical_queues: List[str] = Field(default_factory=list)


class Smell(BaseModel):
    type: str
    severity: Impact = "medium"
    confidence: float = 0.8
    evidence: Dict[str, float | str] = Field(default_factory=dict)


class Recommendation(BaseModel):
    pattern: str
    solution: str
    impact: Impact
    effort: Effort
    priority: int = 99
    reason: str = ""


class Critique(BaseModel):
    pattern_id: str
    level: str  # "warning" | "blocker"
    message: str
    evidence: Dict[str, float | str] = Field(default_factory=dict)


class PlanStep(BaseModel):
    title: str
    description: str
    impact: Impact
    effort: Effort
    depends_on: List[int] = Field(default_factory=list)


class RecommendationRequest(BaseModel):
    # Allow direct submission of canonical signals/topology for the MVP.
    signals: Dict[str, float] = Field(default_factory=dict)
    topology: ServiceTopology = Field(default_factory=ServiceTopology)


class RecommendationResponse(BaseModel):
    smells: List[Smell]
    recommendations: List[Recommendation]
    critiques: List[Critique]
    plan: List[PlanStep]


class GraphState(TypedDict, total=False):
    # Input aliases for telemetry stage
    raw_signals: Dict[str, float]
    raw_topology: Dict[str, Any]

    # Required MVP pipeline state fields
    signals: Dict[str, float]
    topology: Dict[str, Any]
    smells: List[Dict[str, Any]]
    patterns: List[ArchitecturePattern]
    recommendations: List[Recommendation]
    critiques: List[Critique]
    final_plan: List[PlanStep]

