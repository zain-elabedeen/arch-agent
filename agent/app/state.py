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
    request_latency_p50_ms: Optional[float] = None
    request_latency_p90_ms: Optional[float] = None
    request_latency_p99_ms: Optional[float] = None
    db_latency_p95_ms: Optional[float] = None
    error_rate: Optional[float] = None  # 0..1
    status_5xx_rate: Optional[float] = None  # 0..1
    status_4xx_rate: Optional[float] = None  # 0..1
    cpu_utilization: Optional[float] = None  # 0..1
    memory_utilization: Optional[float] = None  # 0..1
    queue_backlog: Optional[float] = None
    saturation: Optional[float] = None  # generic 0..1, optional
    pod_restart_total: Optional[float] = None
    unavailable_replicas: Optional[float] = None
    single_instance_service_count: Optional[float] = None
    hpa_scaling_pressure: Optional[float] = None
    timeout_count: Optional[float] = None
    dependency_error_count: Optional[float] = None
    probe_failure_count: Optional[float] = None
    oom_killed_count: Optional[float] = None
    crash_signal_count: Optional[float] = None


class ServiceSnapshot(BaseModel):
    """Canonical per-service runtime view used by ingestion and future UIs."""

    name: str
    namespace: Optional[str] = None
    cpu: float = 0.0
    memory: float = 0.0
    cpu_usage_cores: Optional[float] = None
    memory_usage_bytes: Optional[float] = None
    replicas: int = 0
    available_replicas: Optional[int] = None
    unavailable_replicas: Optional[int] = None
    restarts: int = 0


class SnapshotSignals(BaseModel):
    """Extensible aggregate signal bag derived from one infrastructure snapshot."""

    model_config = ConfigDict(extra="allow")

    cpu_utilization: Optional[float] = None
    memory_utilization: Optional[float] = None
    queue_backlog: Optional[float] = None
    error_rate: Optional[float] = None
    status_5xx_rate: Optional[float] = None
    status_4xx_rate: Optional[float] = None
    request_latency_p50_ms: Optional[float] = None
    request_latency_p90_ms: Optional[float] = None
    request_latency_p95_ms: Optional[float] = None
    request_latency_p99_ms: Optional[float] = None
    pod_restart_total: Optional[float] = None
    unavailable_replicas: Optional[float] = None
    single_instance_service_count: Optional[float] = None
    hpa_scaling_pressure: Optional[float] = None


class LogEvent(BaseModel):
    """One normalized application/platform log event retained as snapshot evidence."""

    timestamp: str
    service: str
    namespace: str
    pod: str
    level: str
    category: str
    message_sample: str
    is_error: bool
    container: Optional[str] = None
    method: Optional[str] = None
    route: Optional[str] = None
    status_code: Optional[int] = None
    latency_ms: Optional[float] = None
    error_type: Optional[str] = None
    trace_id: Optional[str] = None
    count: int = 1


class LogSignals(BaseModel):
    """Aggregate log-derived signals over one snapshot window."""

    model_config = ConfigDict(extra="allow")

    request_count: float = 0.0
    error_count: float = 0.0
    error_rate: Optional[float] = None
    status_5xx_rate: Optional[float] = None
    status_4xx_rate: Optional[float] = None
    request_latency_p50_ms: Optional[float] = None
    request_latency_p90_ms: Optional[float] = None
    request_latency_p95_ms: Optional[float] = None
    request_latency_p99_ms: Optional[float] = None
    timeout_count: float = 0.0
    dependency_error_count: float = 0.0
    probe_failure_count: float = 0.0
    oom_killed_count: float = 0.0
    crash_signal_count: float = 0.0


class SnapshotLogDataQuality(BaseModel):
    """Completeness and confidence hints for log ingestion and aggregation."""

    logs_enabled: bool = False
    pods_with_logs: int = 0
    pods_without_logs: int = 0
    parse_failures: int = 0
    latency_sample_count: int = 0
    latency_percentiles_reliable: bool = False


class SnapshotLogs(BaseModel):
    """Normalized log evidence and aggregate log signals for one snapshot."""

    events: List[LogEvent] = Field(default_factory=list)
    signals: LogSignals = Field(default_factory=LogSignals)
    service_signals: Dict[str, LogSignals] = Field(default_factory=dict)
    data_quality: SnapshotLogDataQuality = Field(default_factory=SnapshotLogDataQuality)


class SnapshotDataQuality(BaseModel):
    """Collector completeness and inference quality hints for explainability."""

    metrics_server_available: bool = False
    services_with_metrics: int = 0
    services_without_metrics: int = 0
    excluded_namespaces: List[str] = Field(default_factory=list)
    pods_excluded_by_namespace: int = 0
    pods_without_app_label: int = 0
    topology_edges_inferred: int = 0
    topology_confidence: str = "low"
    topology_nodes_without_metrics: int = 0
    topology_edges_low_confidence: int = 0
    topology_external_nodes: int = 0
    topology_missing_labels: int = 0


class TopologyGraphMeta(BaseModel):
    """Graph-level rendering and data-quality metadata."""

    model_config = ConfigDict(extra="allow")

    run_id: Optional[str] = None
    generated_at: Optional[str] = None
    node_count: int = 0
    edge_count: int = 0
    topology_confidence: str = "low"
    data_sources: List[str] = Field(default_factory=list)


class TopologyNode(BaseModel):
    """UI-ready topology graph node."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    display_name: Optional[str] = None
    kind: str = "workload"
    platform: str = "kubernetes"
    namespace: Optional[str] = None
    resource_name: Optional[str] = None
    workload_kind: Optional[str] = None
    status: str = "unknown"
    severity: str = "none"
    replicas: Optional[int] = None
    available_replicas: Optional[int] = None
    unavailable_replicas: Optional[int] = None
    restarts: Optional[int] = None
    cpu_utilization: Optional[float] = None
    memory_utilization: Optional[float] = None
    cpu_usage_cores: Optional[float] = None
    memory_usage_bytes: Optional[float] = None
    request_count: Optional[float] = None
    error_rate: Optional[float] = None
    request_latency_p95_ms: Optional[float] = None
    smell_count: int = 0
    recommendation_count: int = 0
    labels: Dict[str, Any] = Field(default_factory=dict)
    data_sources: List[str] = Field(default_factory=list)
    is_external: bool = False


class TopologyGraphEdge(BaseModel):
    """UI-ready topology graph edge with confidence and evidence."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str
    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    type: str = "unknown"
    direction: str = "outbound"
    status: str = "unknown"
    confidence: float = 0.0
    inferred_from: List[str] = Field(default_factory=list)
    evidence: List[str] = Field(default_factory=list)
    protocol: Optional[str] = None
    port: Optional[int] = None
    data_sources: List[str] = Field(default_factory=list)


class TopologyGraph(BaseModel):
    """UI-ready topology graph payload stored inside the canonical snapshot."""

    nodes: List[TopologyNode] = Field(default_factory=list)
    edges: List[TopologyGraphEdge] = Field(default_factory=list)
    meta: TopologyGraphMeta = Field(default_factory=TopologyGraphMeta)


class TopologyEdge(BaseModel):
    """One directed dependency between two services (API uses ``from`` / ``to`` aliases)."""

    model_config = ConfigDict(populate_by_name=True)

    from_service: str = Field(alias="from")
    to_service: str = Field(alias="to")
    type: str  # e.g. "http", "db", "queue"
    inferred_from: Optional[str] = None
    confidence: Optional[float] = None
    evidence: List[str] = Field(default_factory=list)


class ServiceTopology(BaseModel):
    """Service list plus edges and optional critical infrastructure markers."""

    model_config = ConfigDict(populate_by_name=True)

    services: List[str] = Field(default_factory=list)
    edges: List[TopologyEdge] = Field(default_factory=list)
    service_details: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    critical_stores: List[str] = Field(default_factory=list)
    critical_queues: List[str] = Field(default_factory=list)
    graph: TopologyGraph = Field(default_factory=TopologyGraph)


class ClusterSnapshot(BaseModel):
    """Canonical snapshot persisted by data-foundation connectors."""

    services: List[ServiceSnapshot] = Field(default_factory=list)
    signals: SnapshotSignals = Field(default_factory=SnapshotSignals)
    topology: ServiceTopology = Field(default_factory=ServiceTopology)
    logs: SnapshotLogs = Field(default_factory=SnapshotLogs)
    data_quality: SnapshotDataQuality = Field(default_factory=SnapshotDataQuality)


class AnalysisScope(BaseModel):
    """Stable target scope for smell, recommendation, critique, and plan cards."""

    kind: str = "cluster"
    id: str = "cluster"
    name: str = "Cluster"
    label: str = "Cluster"
    namespace: Optional[str] = None
    node_id: Optional[str] = None


class Smell(BaseModel):
    """API projection of a smell dict from ``smell_rules.detect_smells``."""

    id: Optional[str] = None
    type: str
    severity: Impact = "medium"
    confidence: float = 0.8
    evidence: Dict[str, float | str] = Field(default_factory=dict)
    scope: Optional[AnalysisScope] = None


class Recommendation(BaseModel):
    """One ranked architecture move proposed by the recommendation agent."""

    id: Optional[str] = None
    pattern: str
    solution: str
    impact: Impact
    effort: Effort
    priority: int = 99
    reason: str = ""
    scope: Optional[AnalysisScope] = None
    source_smells: List[str] = Field(default_factory=list)


class Critique(BaseModel):
    """Risk or constraint warning from the critic agent for a given pattern."""

    pattern_id: str
    level: str  # "warning" | "blocker"
    message: str
    evidence: Dict[str, float | str] = Field(default_factory=dict)
    scope: Optional[AnalysisScope] = None


class PlanStep(BaseModel):
    """Single ordered step in the planner output (1-based indexing in ``title`` for MVP)."""

    id: Optional[str] = None
    title: str
    description: str
    impact: Impact
    effort: Effort
    depends_on: List[int] = Field(default_factory=list)
    scope: Optional[AnalysisScope] = None
    recommendation_id: Optional[str] = None


class ScopedAnalysis(BaseModel):
    """Grouped analysis cards for one affected workload, service, cluster, or system scope."""

    scope: AnalysisScope
    smells: List[Smell] = Field(default_factory=list)
    recommendations: List[Recommendation] = Field(default_factory=list)
    critiques: List[Critique] = Field(default_factory=list)
    plan: List[PlanStep] = Field(default_factory=list)


class RecommendationRequest(BaseModel):
    """
    POST body: raw metric bag plus topology (telemetry node normalizes signal keys).

    When ``signals`` is empty and ``topology`` has no services or edges, the API
    loads the latest Kubernetes snapshot from Postgres (requires ``ARCHAGENT_POSTGRES_DSN``).
    """

    signals: Dict[str, float] = Field(default_factory=dict)
    topology: ServiceTopology = Field(default_factory=ServiceTopology)
    logs: Dict[str, Any] = Field(default_factory=dict)


def recommendation_request_has_inline_payload(req: RecommendationRequest) -> bool:
    """True when the client supplied explicit signals or topology to analyze."""
    if req.signals:
        return True
    return bool(req.topology.services or req.topology.edges or req.logs)


class TopologyResponse(BaseModel):
    """GET /v1/topology response backed by the persisted snapshot JSONB."""

    snapshot_run_id: str
    graph: TopologyGraph = Field(default_factory=TopologyGraph)
    data_quality: Dict[str, Any] = Field(default_factory=dict)


class RecommendationResponse(BaseModel):
    """Full pipeline result returned to API clients."""

    snapshot_run_id: Optional[str] = None
    smells: List[Smell]
    recommendations: List[Recommendation]
    critiques: List[Critique]
    plan: List[PlanStep]
    scoped_analysis: List[ScopedAnalysis] = Field(default_factory=list)
    log_analysis: Dict[str, Any] = Field(default_factory=dict)
    explanation_source: str = ""
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
    raw_logs: Dict[str, Any]

    # Canonical inputs after telemetry_agent; then smells → … → explanation_report.
    signals: Dict[str, float]
    topology: Dict[str, Any]
    smells: List[Dict[str, Any]]
    patterns: List[ArchitecturePattern]
    recommendations: List[Recommendation]
    critiques: List[Critique]
    final_plan: List[PlanStep]
    scoped_analysis: List[ScopedAnalysis]
    log_analysis: Dict[str, Any]
    explanation_source: str
    explanation_report: str
