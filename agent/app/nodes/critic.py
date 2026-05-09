"""
Critic agent: surface risks from pattern ``avoid_when`` clauses.

Evaluates string conditions (heuristic rules over signals/topology) and structured
``PatternConstraint`` entries (see ``agent.app.models.pattern``) so warnings stay
grounded in explicit “when not to” knowledge.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from agent.app.logging_utils import get_logger
from agent.app.models.pattern import ArchitecturePattern, PatternConstraint
from agent.app.services.analysis_scoping import cluster_scope, copy_scope
from agent.app.state import Critique, GraphState

logger = get_logger("agent.nodes.critic")


def _get_signal_value(state: GraphState, key: str) -> Optional[float]:
    """Return a numeric signal from normalized ``signals``, else from ``raw_signals``."""
    val = state.get("signals", {}).get(key)
    if val is None:
        # allow looking up raw metric keys too
        raw = state.get("raw_signals", {}).get(key)
        return float(raw) if raw is not None else None
    return float(val)


def _topology_has(state: GraphState, key: str) -> bool:
    """Predicate helpers for topology-shaped ``PatternConstraint`` keys (e.g. ``has_db_edge``)."""
    topology = state.get("topology", {})
    edges = topology.get("edges", [])
    if key == "has_db_edge":
        return any(e.get("type") == "db" for e in edges)
    if key == "has_queue_edge":
        return any(e.get("type") == "queue" for e in edges)
    if key == "has_critical_store":
        return len(topology.get("critical_stores", [])) > 0
    if key == "has_critical_queue":
        return len(topology.get("critical_queues", [])) > 0
    return False


def _eval_structured_constraint(state: GraphState, c: PatternConstraint) -> tuple[bool, Dict[str, Any]]:
    """
    Evaluate one structured ``avoid_when`` clause.

    Returns ``(triggered, evidence)``. Missing signal values yield ``triggered=False``
    so the critic does not warn on absent data.
    """
    evidence: Dict[str, Any] = {"constraint_key": c.key, "operator": c.operator, "kind": c.kind}
    if c.kind == "topology":
        ok = _topology_has(state, c.key)
        evidence["value"] = ok
        # For topology constraints we treat "exists" as the meaningful operator.
        return ok if c.operator == "exists" else ok, evidence

    val = _get_signal_value(state, c.key)
    evidence["value"] = val
    if val is None:
        return False, evidence

    if c.operator == "exists":
        return True, evidence
    if c.value is None:
        return False, evidence

    if c.operator == "gt":
        return val > c.value, evidence
    if c.operator == "gte":
        return val >= c.value, evidence
    if c.operator == "lt":
        return val < c.value, evidence
    if c.operator == "lte":
        return val <= c.value, evidence
    if c.operator == "eq":
        return val == c.value, evidence
    if c.operator == "neq":
        return val != c.value, evidence

    return False, evidence


# Human-authored pattern JSON may use variant phrases; normalize before rule lookup.
CONDITION_ALIASES: Dict[str, str] = {
    "very small systems": "small simple systems",
    "low throughput systems": "low traffic systems",
    "unstable external services": "persistent failures",
}


# Stable user-facing copy when a string ``avoid_when`` condition fires.
WARNING_TEMPLATES: Dict[str, str] = {
    "write-heavy workload": "Workload appears write-heavy; this pattern targets read scaling and may not address the bottleneck.",
    "strict consistency required": "This pattern may introduce eventual consistency and violate strong consistency requirements.",
    "highly dynamic data": "Data changes frequently; caching/replication may increase stale-read risk.",
    "low traffic systems": "System load is low; this pattern may add unnecessary complexity.",
    "persistent failures": "Failures appear persistent; retry-based strategies may amplify load and worsen performance.",
    "transient failures": "Failures appear intermittent; controlled retries may help but should be rate-limited.",
    "stateful tightly coupled services": "Topology appears stateful and tightly coupled; scaling/distribution patterns may be limited.",
    "single instance systems": "System appears to be a single instance; distribution patterns may not apply yet.",
    "complex join-heavy queries": "Queries are join-heavy; sharding may significantly increase query complexity.",
    "very stable dependencies": "Dependencies are stable; resilience patterns may add overhead without clear benefit.",
    "high load conditions": "System is under high load; additional retries/processing may worsen performance.",
    "resource exhaustion": "Resources are near limits; adding overhead may exacerbate pressure.",
}


def _normalize_condition(condition: str) -> str:
    """Lowercase and map catalog synonyms to keys in ``AVOID_CONDITION_RULES``."""
    c = condition.strip().lower()
    return CONDITION_ALIASES.get(c, c)


def _sig(signals: Dict[str, Any], key: str) -> Any:
    """Read optional extended signal the smell layer may not populate (critic-only hints)."""
    return signals.get(key)


# Phrase-condition evaluators for string ``avoid_when`` entries: return True when the
# warning applies, False when it does not, None if required signals/topology are absent.
# Keys align with ``AVOID_CONDITION_RULES`` after ``_normalize_condition``.


def _rule_write_heavy(signals: Dict[str, Any], topology: Dict[str, Any]) -> Optional[bool]:
    """``write_ratio`` > 0.6 ⇒ write-heavy (read-scaling patterns may misfit)."""
    v = _sig(signals, "write_ratio")
    return None if v is None else float(v) > 0.6


def _rule_read_heavy(signals: Dict[str, Any], topology: Dict[str, Any]) -> Optional[bool]:
    """``read_ratio`` > 0.7 ⇒ read-heavy context."""
    v = _sig(signals, "read_ratio")
    return None if v is None else float(v) > 0.7


def _rule_low_traffic(signals: Dict[str, Any], topology: Dict[str, Any]) -> Optional[bool]:
    """``request_rate`` < 50 ⇒ low traffic (complex patterns may be premature)."""
    v = _sig(signals, "request_rate")
    return None if v is None else float(v) < 50


def _rule_increasing_traffic(signals: Dict[str, Any], topology: Dict[str, Any]) -> Optional[bool]:
    """``request_growth_rate`` > 0.2 ⇒ sustained growth (capacity patterns more relevant)."""
    v = _sig(signals, "request_growth_rate")
    return None if v is None else float(v) > 0.2


def _rule_strict_consistency(signals: Dict[str, Any], topology: Dict[str, Any]) -> Optional[bool]:
    """``requires_strong_consistency`` flag from extended signals (eventual-consistency risk)."""
    v = _sig(signals, "requires_strong_consistency")
    return None if v is None else bool(v)


def _rule_highly_dynamic(signals: Dict[str, Any], topology: Dict[str, Any]) -> Optional[bool]:
    """``data_volatility`` > 0.7 ⇒ cache/replica staleness risk."""
    v = _sig(signals, "data_volatility")
    return None if v is None else float(v) > 0.7


def _rule_frequent_write_read(signals: Dict[str, Any], topology: Dict[str, Any]) -> Optional[bool]:
    """``write_read_coupling`` > 0.6 ⇒ tight write/read coupling on hot paths."""
    v = _sig(signals, "write_read_coupling")
    return None if v is None else float(v) > 0.6


def _rule_small_dataset(signals: Dict[str, Any], topology: Dict[str, Any]) -> Optional[bool]:
    """``dataset_size_gb`` < 5 ⇒ sharding/partitioning may be overkill."""
    v = _sig(signals, "dataset_size_gb")
    return None if v is None else float(v) < 5


def _rule_small_simple(signals: Dict[str, Any], topology: Dict[str, Any]) -> Optional[bool]:
    """Fewer than three services in topology ⇒ “small simple” deployment shape."""
    service_count = topology.get("service_count")
    if service_count is None:
        services = topology.get("services")
        if services is None:
            return None
        service_count = len(services)
    return int(service_count) < 3


def _rule_persistent_failures(signals: Dict[str, Any], topology: Dict[str, Any]) -> Optional[bool]:
    """``error_rate`` > 0.3 ⇒ retries/circuits may amplify load (treat as persistent)."""
    v = _sig(signals, "error_rate")
    return None if v is None else float(v) > 0.3


def _rule_transient_failures(signals: Dict[str, Any], topology: Dict[str, Any]) -> Optional[bool]:
    """Elevated but moderate ``error_rate`` band (transient / flaky behavior)."""
    v = _sig(signals, "error_rate")
    if v is None:
        return None
    return 0.05 < float(v) < 0.3


def _rule_stable_deps(signals: Dict[str, Any], topology: Dict[str, Any]) -> Optional[bool]:
    """``dependency_failure_rate`` < 0.01 ⇒ resilience churn may not pay off."""
    v = _sig(signals, "dependency_failure_rate")
    return None if v is None else float(v) < 0.01


def _rule_high_load(signals: Dict[str, Any], topology: Dict[str, Any]) -> Optional[bool]:
    """High CPU (>0.8) or very high ``request_rate`` (>1000) ⇒ load pressure."""
    cpu = _sig(signals, "cpu") or _sig(signals, "cpu_utilization")
    req = _sig(signals, "request_rate")
    if cpu is None and req is None:
        return None
    return (float(cpu) > 0.8 if cpu is not None else False) or (float(req) > 1000 if req is not None else False)


def _rule_resource_exhaustion(signals: Dict[str, Any], topology: Dict[str, Any]) -> Optional[bool]:
    """CPU or memory above 0.9 ⇒ adding overhead may worsen saturation."""
    cpu = _sig(signals, "cpu") or _sig(signals, "cpu_utilization")
    mem = _sig(signals, "memory") or _sig(signals, "memory_utilization")
    if cpu is None and mem is None:
        return None
    return (float(cpu) > 0.9 if cpu is not None else False) or (float(mem) > 0.9 if mem is not None else False)


def _rule_traffic_spikes(signals: Dict[str, Any], topology: Dict[str, Any]) -> Optional[bool]:
    """Boolean ``traffic_spike`` hint from extended signals."""
    v = _sig(signals, "traffic_spike")
    return None if v is None else bool(v)


def _rule_stateful_tight(signals: Dict[str, Any], topology: Dict[str, Any]) -> Optional[bool]:
    """``stateful`` and high ``coupling_score`` in topology metadata."""
    st = topology.get("stateful")
    score = topology.get("coupling_score")
    if st is None or score is None:
        return None
    return bool(st) and float(score) > 0.7


def _rule_single_instance(signals: Dict[str, Any], topology: Dict[str, Any]) -> Optional[bool]:
    """``instance_count`` == 1 ⇒ horizontal patterns may not apply yet."""
    cnt = topology.get("instance_count")
    return None if cnt is None else int(cnt) == 1


def _rule_very_small(signals: Dict[str, Any], topology: Dict[str, Any]) -> Optional[bool]:
    """Fewer than two services ⇒ minimal topology."""
    service_count = topology.get("service_count")
    if service_count is None:
        services = topology.get("services")
        if services is None:
            return None
        service_count = len(services)
    return int(service_count) < 2


def _rule_complex_joins(signals: Dict[str, Any], topology: Dict[str, Any]) -> Optional[bool]:
    """``complex_query_ratio`` > 0.5 ⇒ sharding may complicate query paths."""
    v = _sig(signals, "complex_query_ratio")
    return None if v is None else float(v) > 0.5


# Maps normalized ``avoid_when`` string labels to evaluator callables.
AVOID_CONDITION_RULES = {
    "write-heavy workload": _rule_write_heavy,
    "read-heavy workload": _rule_read_heavy,
    "low traffic systems": _rule_low_traffic,
    "increasing traffic": _rule_increasing_traffic,
    "strict consistency required": _rule_strict_consistency,
    "highly dynamic data": _rule_highly_dynamic,
    "frequent write-read dependency": _rule_frequent_write_read,
    "small dataset": _rule_small_dataset,
    "small simple systems": _rule_small_simple,
    "persistent failures": _rule_persistent_failures,
    "transient failures": _rule_transient_failures,
    "very stable dependencies": _rule_stable_deps,
    "high load conditions": _rule_high_load,
    "resource exhaustion": _rule_resource_exhaustion,
    "traffic spikes": _rule_traffic_spikes,
    "stateful tightly coupled services": _rule_stateful_tight,
    "single instance systems": _rule_single_instance,
    "very small systems": _rule_very_small,
    "complex join-heavy queries": _rule_complex_joins,
    "low throughput systems": _rule_low_traffic,
    "unstable external services": _rule_persistent_failures,
}


def _generate_phrase_warning(
    pattern: ArchitecturePattern, raw_condition: str, signals: Dict[str, Any], topology: Dict[str, Any]
) -> Optional[Critique]:
    """
    If ``raw_condition`` matches a registered rule and the rule returns True,
    emit a templated ``Critique`` for that pattern.
    """
    condition = _normalize_condition(raw_condition)
    rule = AVOID_CONDITION_RULES.get(condition)
    if not rule:
        return None
    try:
        violated = rule(signals, topology)
    except Exception:
        return None
    if violated is None or not violated:
        return None
    message = WARNING_TEMPLATES.get(condition, f"{pattern.name} may not be suitable: {condition}")
    return Critique(
        pattern_id=pattern.id,
        level="warning",
        message=message,
        evidence={"condition": condition},
    )


def critique_patterns(state: GraphState, patterns: List[ArchitecturePattern]) -> List[Critique]:
    """
    Walk each pattern's ``avoid_when``: string phrases use heuristics above;
    ``PatternConstraint`` entries use structured evaluation against ``state``.
    """
    critiques: List[Critique] = []
    signals = state.get("signals", {}) or {}
    topology = state.get("topology", {}) or {}
    for p in patterns:
        for c in p.avoid_when:
            if isinstance(c, str):
                warning = _generate_phrase_warning(p, c, signals, topology)
                if warning is not None:
                    critiques.append(warning)
                continue

            triggered, evidence = _eval_structured_constraint(state, c)
            if not triggered:
                continue
            critiques.append(
                Critique(
                    pattern_id=p.id,
                    level="warning",
                    message=c.message or f"{p.name} may not be suitable under current conditions.",
                    evidence={k: str(v) for k, v in evidence.items()},
                )
            )
    return critiques


def _scope_critiques_to_recommendations(state: GraphState, critiques: List[Critique]) -> List[Critique]:
    """Attach critique warnings to every scoped recommendation using the same pattern."""
    recommendations = state.get("recommendations", []) or []
    recs_by_pattern: Dict[str, List[Any]] = {}
    for rec in recommendations:
        pattern_id = getattr(rec, "pattern", None) or (rec.get("pattern") if isinstance(rec, dict) else None)
        if pattern_id:
            recs_by_pattern.setdefault(str(pattern_id), []).append(rec)

    if not recs_by_pattern:
        return critiques

    scoped: List[Critique] = []
    for critique in critiques:
        matches = recs_by_pattern.get(critique.pattern_id) or []
        if not matches:
            scoped.append(critique.model_copy(update={"scope": cluster_scope()}))
            continue
        for rec in matches:
            rec_scope = getattr(rec, "scope", None) or (rec.get("scope") if isinstance(rec, dict) else None)
            scoped.append(critique.model_copy(update={"scope": copy_scope(rec_scope)}))
    return scoped


def critic_node(state: GraphState) -> GraphState:
    """
    Critic node: apply avoid_when constraints to surface risks/warnings.
    """

    run_id = state.get("run_id", "n/a")
    logger.info(
        "critic_agent start run_id=%s patterns=%d recommendations=%d",
        run_id,
        len(state.get("patterns", [])),
        len(state.get("recommendations", [])),
    )
    # Critiques are generated automatically from pattern constraints + runtime state.
    state["critiques"] = _scope_critiques_to_recommendations(state, critique_patterns(state, state.get("patterns", [])))
    logger.info(
        "critic_agent done run_id=%s critiques=%d",
        run_id,
        len(state.get("critiques", [])),
    )
    return state
