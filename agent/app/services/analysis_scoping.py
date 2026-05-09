"""Scope architecture analysis to concrete topology targets.

The smell rules stay intentionally deterministic and simple. This layer turns
their service evidence into UI-ready workload scopes so recommendations can be
rendered and filtered by topology node without parsing evidence strings.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional

from agent.app.state import AnalysisScope, Critique, PlanStep, Recommendation, ScopedAnalysis, Smell

_COUNT_FIELDS_THAT_SPLIT_PER_SERVICE = {"single_instance_service_count"}
_SERVICE_SUMMARY_KEYS = {
    "request_count",
    "error_count",
    "error_rate",
    "status_5xx_rate",
    "status_4xx_rate",
    "request_latency_p50_ms",
    "request_latency_p90_ms",
    "request_latency_p95_ms",
    "request_latency_p99_ms",
    "timeout_count",
    "dependency_error_count",
    "probe_failure_count",
    "oom_killed_count",
    "crash_signal_count",
}


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_.-]+", "-", value.lower().strip())
    slug = re.sub(r"-+", "-", slug).strip("-.")
    return slug or "unknown"


def cluster_scope() -> AnalysisScope:
    """Default scope for findings that cannot be safely tied to one workload."""
    return AnalysisScope(kind="cluster", id="cluster", name="Cluster", label="Cluster")


def system_scope() -> AnalysisScope:
    """Reserved scope for ArchAgent/system-level findings."""
    return AnalysisScope(kind="system", id="system", name="System", label="System")


def coerce_scope(scope: Any) -> AnalysisScope:
    if isinstance(scope, AnalysisScope):
        return scope
    if isinstance(scope, dict):
        return AnalysisScope.model_validate(scope)
    return cluster_scope()


def scope_key(scope: Any) -> str:
    return coerce_scope(scope).id


def _scope_for_graph_node(node: Dict[str, Any]) -> Optional[AnalysisScope]:
    if not node.get("id") or not node.get("name") or node.get("is_external"):
        return None
    namespace = node.get("namespace")
    name = str(node["name"])
    label = f"{name} / {namespace}" if namespace else name
    return AnalysisScope(
        kind="workload",
        id=str(node["id"]),
        name=name,
        label=label,
        namespace=str(namespace) if namespace else None,
        node_id=str(node["id"]),
    )


def _fallback_workload_scope(name: str, detail: Dict[str, Any]) -> AnalysisScope:
    namespace = detail.get("namespace")
    node_id = f"k8s:{_slug(str(namespace or 'unknown'))}:workload:{_slug(name)}"
    label = f"{name} / {namespace}" if namespace else name
    return AnalysisScope(
        kind="workload",
        id=node_id,
        name=name,
        label=label,
        namespace=str(namespace) if namespace else None,
        node_id=node_id,
    )


def _service_details(topology: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    details = topology.get("service_details") if isinstance(topology, dict) else {}
    if not isinstance(details, dict):
        return {}
    return {str(name): dict(detail or {}) for name, detail in details.items()}


def build_scope_index(topology: Dict[str, Any]) -> Dict[str, Any]:
    """Build scope lookup maps from topology graph nodes, with service-details fallback."""
    scopes_by_name: Dict[str, List[AnalysisScope]] = {}
    scopes_by_node_id: Dict[str, AnalysisScope] = {}

    graph = topology.get("graph") if isinstance(topology, dict) else {}
    graph_nodes = graph.get("nodes") if isinstance(graph, dict) else []
    for raw_node in graph_nodes or []:
        if not isinstance(raw_node, dict):
            continue
        scope = _scope_for_graph_node(raw_node)
        if scope is None:
            continue
        scopes_by_name.setdefault(scope.name, []).append(scope)
        if scope.node_id:
            scopes_by_node_id[scope.node_id] = scope

    if not scopes_by_name:
        details = _service_details(topology)
        names = set(topology.get("services") or []) | set(details)
        for raw_name in sorted(str(name) for name in names if str(name)):
            scope = _fallback_workload_scope(raw_name, details.get(raw_name, {}))
            scopes_by_name.setdefault(scope.name, []).append(scope)
            if scope.node_id:
                scopes_by_node_id[scope.node_id] = scope

    return {"by_name": scopes_by_name, "by_node_id": scopes_by_node_id}


def evidence_services(evidence: Dict[str, Any]) -> List[str]:
    """Return service names encoded as either ``service`` or comma/list ``services``."""
    names: List[str] = []
    raw = evidence.get("services")
    if isinstance(raw, str):
        names.extend(part.strip() for part in raw.split(",") if part.strip())
    elif isinstance(raw, list):
        names.extend(str(part).strip() for part in raw if str(part).strip())

    single = evidence.get("service")
    if single:
        names.append(str(single).strip())

    seen: set[str] = set()
    out: List[str] = []
    for name in names:
        if name and name not in seen:
            out.append(name)
            seen.add(name)
    return out


def _resolve_service_scope(service: str, evidence: Dict[str, Any], index: Dict[str, Any]) -> Optional[AnalysisScope]:
    candidates = list(index.get("by_name", {}).get(service) or [])
    if not candidates:
        return None
    namespace = evidence.get("namespace")
    if namespace:
        matches = [scope for scope in candidates if scope.namespace == str(namespace)]
        if len(matches) == 1:
            return matches[0]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _numeric(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _narrow_evidence(
    evidence: Dict[str, Any],
    *,
    service: str | None,
    scope: AnalysisScope,
    service_count: int,
    topology: Dict[str, Any],
) -> Dict[str, Any]:
    narrowed = dict(evidence)
    narrowed["scope_id"] = scope.id
    if scope.node_id:
        narrowed["node_id"] = scope.node_id
    if scope.namespace:
        narrowed["namespace"] = scope.namespace
    if service:
        narrowed["service"] = service
        narrowed["services"] = service
        for key in _COUNT_FIELDS_THAT_SPLIT_PER_SERVICE:
            value = _numeric(narrowed.get(key))
            if value is not None and service_count > 1:
                narrowed[key] = 1.0
        detail = _service_details(topology).get(service) or {}
        summary = detail.get("log_summary") if isinstance(detail, dict) else {}
        if isinstance(summary, dict):
            for key in _SERVICE_SUMMARY_KEYS:
                if key in summary:
                    narrowed[key] = summary[key]
    return {key: value for key, value in narrowed.items() if isinstance(value, (int, float, str))}


def _scoped_smell_id(smell: Dict[str, Any], scope: AnalysisScope, suffix: str | None = None) -> str:
    base = f"{scope.id}:{smell.get('type', 'unknown')}"
    return f"{base}:{_slug(suffix)}" if suffix else base


def scope_smells(smells: Iterable[Dict[str, Any]], topology: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Split raw smell dicts into explicitly scoped smell dicts."""
    index = build_scope_index(topology)
    scoped: List[Dict[str, Any]] = []

    for smell in smells:
        evidence = dict(smell.get("evidence") or {})
        services = evidence_services(evidence)
        if not services:
            scope = cluster_scope()
            scoped.append(
                {
                    **smell,
                    "id": _scoped_smell_id(smell, scope),
                    "evidence": _narrow_evidence(evidence, service=None, scope=scope, service_count=0, topology=topology),
                    "scope": scope.model_dump(),
                }
            )
            continue

        unresolved: List[str] = []
        for service in services:
            resolved = _resolve_service_scope(service, evidence, index)
            if resolved is None:
                unresolved.append(service)
                continue
            scoped.append(
                {
                    **smell,
                    "id": _scoped_smell_id(smell, resolved),
                    "evidence": _narrow_evidence(
                        evidence,
                        service=service,
                        scope=resolved,
                        service_count=len(services),
                        topology=topology,
                    ),
                    "scope": resolved.model_dump(),
                }
            )

        if unresolved:
            scope = cluster_scope()
            cluster_evidence = dict(evidence)
            cluster_evidence["unresolved_services"] = ", ".join(unresolved)
            scoped.append(
                {
                    **smell,
                    "id": _scoped_smell_id(smell, scope, ",".join(unresolved)),
                    "evidence": _narrow_evidence(
                        cluster_evidence,
                        service=None,
                        scope=scope,
                        service_count=len(services),
                        topology=topology,
                    ),
                    "scope": scope.model_dump(),
                }
            )

    return scoped


def _group_for(groups: "OrderedDict[str, ScopedAnalysis]", scope: AnalysisScope) -> ScopedAnalysis:
    group = groups.get(scope.id)
    if group is None:
        group = ScopedAnalysis(scope=scope)
        groups[scope.id] = group
    return group


def build_scoped_analysis(
    smells: Iterable[Any],
    recommendations: Iterable[Any],
    critiques: Iterable[Any],
    plan: Iterable[Any],
) -> List[ScopedAnalysis]:
    """Group final pipeline outputs by their attached ``AnalysisScope``."""
    groups: "OrderedDict[str, ScopedAnalysis]" = OrderedDict()

    for item in smells:
        smell = Smell.model_validate(item)
        _group_for(groups, coerce_scope(smell.scope)).smells.append(smell)
    for item in recommendations:
        recommendation = Recommendation.model_validate(item)
        _group_for(groups, coerce_scope(recommendation.scope)).recommendations.append(recommendation)
    for item in critiques:
        critique = Critique.model_validate(item)
        _group_for(groups, coerce_scope(critique.scope)).critiques.append(critique)
    for item in plan:
        step = PlanStep.model_validate(item)
        _group_for(groups, coerce_scope(step.scope)).plan.append(step)

    return list(groups.values())


def scope_label(scope: Any) -> str:
    resolved = coerce_scope(scope)
    return resolved.label or resolved.name or resolved.id


def copy_scope(scope: Any) -> AnalysisScope:
    """Create a detached scope model from dict/BaseModel/None input."""
    return coerce_scope(scope).model_copy()
