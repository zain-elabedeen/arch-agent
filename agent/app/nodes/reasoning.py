"""
Reasoning / explanation agent (terminal pipeline stage).

Produces ``explanation_report`` markdown from smells, recommendations, and
critiques. An optional LLM may **rephrase** the deterministic report only; if the
model invents or drops facts, output is rejected in favor of the template report.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from agent.app.config import Settings
from agent.app.logging_utils import get_logger
from agent.app.models.pattern import ArchitecturePattern
from agent.app.state import Critique, GraphState, Recommendation

logger = get_logger("agent.nodes.reasoning")

_SMELL_EXPLAINERS: Dict[str, str] = {
    "read_scaling_bottleneck": "Read paths appear to be contributing to elevated request or database latency.",
    "cpu_saturation": "One or more services are close to CPU capacity, so extra traffic may turn into latency or failures.",
    "memory_pressure": "One or more services are close to memory capacity, which can cause restarts, throttling, or degraded latency.",
    "queue_backlog": "Work is accumulating faster than consumers can process it, which usually points to throughput mismatch.",
    "restart_instability": "Pods are restarting often enough to suggest instability, crash loops, resource pressure, or dependency failures.",
    "replica_unavailability": "Desired capacity is not fully available, so the system may have less redundancy than expected.",
    "autoscaling_pressure": "Autoscaling wants more capacity than is currently available, which suggests demand is exceeding current pods.",
    "single_instance_risk": "At least one service has only one instance, so a single pod failure can remove that service's capacity.",
    "coupling_risk": "A service has many outbound dependencies, increasing coordination, failure propagation, and change risk.",
    "high_error_rate": "The observed error rate is high enough to require resilience and failure-containment patterns.",
    "error_burst": "Recent logs show an elevated error or 5xx rate in the snapshot window.",
    "timeout_pressure": "Recent logs show repeated timeout behavior, which often points to slow or overloaded dependencies.",
    "dependency_instability": "Recent logs show dependency connection or resolution failures that can propagate upstream.",
    "probe_instability": "Recent logs show health probe failures; this is operational evidence to validate before broad architecture changes.",
    "crash_loop_signal": "Recent logs show crash or OOM-style evidence that may explain instability or restart behavior.",
}

_LOG_EVIDENCE_KEYS = {
    "status_5xx_rate",
    "status_4xx_rate",
    "request_count",
    "timeout_count",
    "dependency_error_count",
    "probe_failure_count",
    "oom_killed_count",
    "crash_signal_count",
}


def _format_evidence(evidence: Dict[str, Any]) -> str:
    if not evidence:
        return "No detailed evidence fields were attached."
    parts = []
    for key in sorted(evidence.keys()):
        value = evidence[key]
        if isinstance(value, float):
            parts.append(f"`{key}`={value:g}")
        else:
            parts.append(f"`{key}`={value}")
    return ", ".join(parts)


def _evidence_services(evidence: Dict[str, Any]) -> List[str]:
    raw = evidence.get("services")
    if not raw:
        service = evidence.get("service")
        return [str(service)] if service else []
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    return []


def _affected_services_from_smells(smells: List[dict]) -> List[str]:
    services: set[str] = set()
    for smell in smells:
        services.update(_evidence_services(smell.get("evidence", {}) or {}))
    return sorted(services)


def _pattern_lookup(patterns: List[ArchitecturePattern]) -> Dict[str, ArchitecturePattern]:
    return {p.id: p for p in patterns}


def _service_context_lines(state: GraphState, affected_services: List[str]) -> List[str]:
    topology = state.get("topology", {}) or {}
    service_details = topology.get("service_details", {}) if isinstance(topology, dict) else {}
    services = topology.get("services", []) if isinstance(topology, dict) else []
    edges = topology.get("edges", []) if isinstance(topology, dict) else []

    lines = [
        f"- The snapshot contains {len(services)} service(s) and {len(edges)} inferred dependency edge(s).",
    ]
    if affected_services:
        lines.append(f"- The current findings affect: `{', '.join(affected_services)}`.")
    else:
        lines.append("- No specific affected service was attached to the current findings.")

    for service in affected_services:
        detail = service_details.get(service, {}) if isinstance(service_details, dict) else {}
        if not isinstance(detail, dict):
            continue
        ns = detail.get("namespace") or "unknown namespace"
        replicas = detail.get("replicas", "unknown")
        available = detail.get("available_replicas", "unknown")
        restarts = detail.get("restarts", "unknown")
        lines.append(
            f"- `{service}` is running in `{ns}` with replicas={replicas}, available_replicas={available}, restarts={restarts}."
        )
    return lines


def _story_lines(smells: List[dict], recommendations: List[Recommendation], affected_services: List[str]) -> List[str]:
    if not smells:
        return [
            "- The latest snapshot does not show an architecture stress signal that crosses the current MVP thresholds.",
            "- That does not prove the system is perfect; it means this snapshot does not currently justify an architecture change from the deterministic rules.",
        ]

    smell_names = ", ".join(f"`{s.get('type', 'unknown')}`" for s in smells)
    pattern_names = ", ".join(f"`{r.pattern}`" for r in recommendations) if recommendations else "no pattern"
    target = f" for `{', '.join(affected_services)}`" if affected_services else ""
    return [
        f"- The system is telling a simple architecture story: {smell_names} was detected{target}.",
        f"- The recommendation engine translated that smell into {pattern_names} because those patterns address the structural risk represented by the signal.",
        "- Read this as a design review prompt: confirm the workload role, decide whether the service is meant to be redundant, then apply the smallest architecture change that removes the risk.",
    ]


def _smell_lines(smells: List[dict]) -> List[str]:
    """Format smell dicts as teaching-oriented markdown bullets."""
    if not smells:
        return [
            "- No architecture smells were detected from the provided runtime signals.",
            "- This means the current snapshot did not cross the deterministic thresholds used by the MVP rules.",
        ]
    lines: List[str] = []
    for s in smells:
        smell_type = s.get("type", "unknown")
        evidence = s.get("evidence", {}) or {}
        services = _evidence_services(evidence)
        explainer = _SMELL_EXPLAINERS.get(smell_type, "This smell indicates an architecture stress signal worth reviewing.")
        service_text = f" Affected service(s): `{', '.join(services)}`." if services else ""
        lines.extend(
            [
                f"- `{smell_type}`",
                f"  - Severity/confidence: {s.get('severity', 'n/a')} / {s.get('confidence', 'n/a')}",
                f"  - What it means: {explainer}",
                f"  - Evidence: {_format_evidence(evidence)}.{service_text}",
            ]
        )
        if set(evidence.keys()) & _LOG_EVIDENCE_KEYS:
            lines.append(
                "  - Source note: this smell is backed by normalized log evidence from the latest snapshot window; treat log samples as supporting evidence, not a complete root-cause proof."
            )
    return lines


def _recommendation_lines(
    recommendations: List[Recommendation],
    patterns_by_id: Dict[str, ArchitecturePattern],
    affected_services: List[str],
) -> List[str]:
    """Format recommendations as teaching-oriented markdown bullets."""
    if not recommendations:
        return [
            "- No architecture changes are currently recommended.",
            "- The system should continue collecting snapshots; future recommendations will appear when smells are detected.",
        ]
    lines: List[str] = []
    service_text = f"`{', '.join(affected_services)}`" if affected_services else "the affected service(s)"
    for r in recommendations:
        pattern = patterns_by_id.get(r.pattern)
        summary = pattern.summary if pattern else "This pattern is mapped from the detected smell."
        tradeoffs = "; ".join(pattern.tradeoffs[:3]) if pattern and pattern.tradeoffs else "No catalog tradeoffs were attached."
        solutions = "; ".join(pattern.solutions[:3]) if pattern and pattern.solutions else r.solution
        lines.extend(
            [
                f"#### `{r.pattern}`",
                f"- Affected service scope: {service_text}.",
                f"- Why it matched: {r.reason or 'Mapped from detected smell.'}",
                f"- Architecture explanation: {summary}",
                f"- How to think about the change: this pattern changes the service shape, not just a metric. The goal is to reduce the structural weakness that produced the smell.",
                f"- Concrete implementation moves: {solutions}.",
                f"- First concrete move from the planner: {r.solution}.",
                f"- Expected benefit: {r.impact} impact if the smell is valid for this workload.",
                f"- Delivery effort: {r.effort}.",
                f"- Tradeoffs to understand before changing production: {tradeoffs}.",
            ]
        )
    return lines


def _critique_lines(critiques: List[Critique]) -> List[str]:
    """Format critiques as markdown bullets."""
    if not critiques:
        return ["- No constraint warnings were triggered by current runtime context."]
    lines: List[str] = []
    for c in critiques:
        lines.append(f"- `{c.pattern_id}` [{c.level}]: {c.message}")
    return lines


def _log_analysis_lines(log_analysis: Dict[str, Any]) -> List[str]:
    """Format optional LLM log analysis as experimental context only."""
    if not log_analysis:
        return ["- No experimental log analysis was attached to this run."]
    if log_analysis.get("disabled_reason"):
        return [f"- Experimental log analysis was skipped: `{log_analysis['disabled_reason']}`."]
    if log_analysis.get("ignored_reason"):
        return [f"- Experimental log analysis was ignored: `{log_analysis['ignored_reason']}`."]

    lines = [
        "- Experimental Gemini log analysis was attached as sidecar context only; it did not create smells or recommendations.",
    ]
    if log_analysis.get("category"):
        lines.append(f"- Dominant category: `{log_analysis['category']}`.")
    if log_analysis.get("suspected_component"):
        lines.append(f"- Suspected component: `{log_analysis['suspected_component']}`.")
    if log_analysis.get("confidence") is not None:
        lines.append(f"- Classifier confidence: {log_analysis['confidence']}.")
    if log_analysis.get("summary"):
        lines.append(f"- Summary: {log_analysis['summary']}")
    terms = log_analysis.get("evidence_terms")
    if isinstance(terms, list) and terms:
        rendered = ", ".join(f"`{term}`" for term in terms[:8])
        lines.append(f"- Evidence terms: {rendered}.")
    return lines


def _plan_lines(recommendations: List[Recommendation], plan_steps: List[Any]) -> List[str]:
    """Explain plan ordering without changing planner decisions."""
    if not plan_steps:
        return ["- No execution plan was produced because there are no active recommendations."]
    lines = [
        "- The planner orders recommendations using impact, effort, and recommendation priority.",
        "- Treat these as architecture investigation steps first; production changes still need owner review, testing, and rollout planning.",
    ]
    for idx, step in enumerate(plan_steps, start=1):
        title = getattr(step, "title", None) or step.get("title", f"Step {idx}")
        description = getattr(step, "description", None) or step.get("description", "")
        lines.append(f"- {title}: {description}")
    rec_ids = [r.pattern for r in recommendations]
    if "horizontal_scaling" in rec_ids and "load_balancing" in rec_ids:
        lines.extend(
            [
                "- Architecture sequencing note: horizontal scaling and load balancing are related patterns. Scaling creates additional instances; load balancing makes those instances useful by distributing traffic across them.",
                "- Learning note: load balancing only helps after more than one healthy instance exists; in practice, add replicas and verify service routing together.",
            ]
        )
    return lines


def _review_questions(smells: List[dict], recommendations: List[Recommendation], affected_services: List[str]) -> List[str]:
    if not recommendations:
        return ["- No review questions are needed until a smell produces a recommendation."]
    service_text = f" for `{', '.join(affected_services)}`" if affected_services else ""
    questions = [
        f"- Is the affected workload{service_text} intended to be highly available, or is one replica acceptable for this environment?",
        "- Does the service keep local state that would make multiple replicas unsafe or ineffective?",
        "- Are readiness/liveness probes configured so traffic only reaches healthy pods?",
        "- If replicas are added, is there a Service, ingress, or gateway path that will actually distribute traffic?",
    ]
    rec_ids = {r.pattern for r in recommendations}
    if "horizontal_scaling" in rec_ids:
        questions.append("- What replica count should be the minimum safe baseline, and should HPA manage it later?")
    if "load_balancing" in rec_ids:
        questions.append("- Does the current traffic path preserve sessions or require sticky routing?")
    return questions


def build_explanation_report(state: GraphState) -> str:
    """
    Explanation-only layer.
    This function must not detect smells or choose architecture decisions.
    It only summarizes deterministic outputs from prior agents.
    """
    smells = state.get("smells", [])
    recommendations = state.get("recommendations", [])
    critiques = state.get("critiques", [])
    plan_steps = state.get("final_plan", [])
    log_analysis = state.get("log_analysis", {}) or {}
    patterns_by_id = _pattern_lookup(state.get("patterns", []) or [])
    affected_services = _affected_services_from_smells(smells)

    report_parts = [
        "## Runtime Architecture Report",
        "",
        "### What This Report Is Doing",
        "This report explains how the deterministic architecture agents interpreted the latest infrastructure snapshot. It connects observed runtime/topology signals to architecture patterns so the user can understand both the recommendation and the design concept behind it.",
        "",
        "The output is guidance for engineering review, not an automatic production change. A human owner should confirm service intent, workload criticality, and rollout constraints before acting.",
        "",
        "### System Story",
        *_story_lines(smells, recommendations, affected_services),
        "",
        "### Affected Services",
        *_service_context_lines(state, affected_services),
        "",
        "### Detected Smells",
        *_smell_lines(smells),
        "",
        "### Recommended Architecture Changes",
        *_recommendation_lines(recommendations, patterns_by_id, affected_services),
        "",
        "### Constraints and Warnings",
        *_critique_lines(critiques),
        "",
        "### Experimental Log Analysis",
        *_log_analysis_lines(log_analysis),
        "",
        "### Execution Plan Rationale",
        *_plan_lines(recommendations, plan_steps),
        "",
        "### Questions To Validate Before Acting",
        *_review_questions(smells, recommendations, affected_services),
        "",
        "### Summary",
        (
            f"Detected {len(smells)} smell(s), produced {len(recommendations)} recommendation(s), "
            f"and raised {len(critiques)} critique warning(s)."
        ),
    ]
    return "\n".join(report_parts).strip()


def _build_llm_prompt(state: GraphState) -> str:
    """User message instructing the model to produce a teaching-oriented rewrite."""
    base_report = build_explanation_report(state)
    return (
        "You are an infrastructure architecture educator rewriting an existing deterministic report.\n"
        "IMPORTANT RULES:\n"
        "- Do NOT add new smells, recommendations, critiques, metrics, services, or evidence.\n"
        "- Do NOT remove any factual claim from the report.\n"
        "- Do NOT claim an action was executed; recommendations are review guidance only.\n"
        "- You may explain architecture concepts that are already named in the report, such as replicas, load balancing, bulkheads, or horizontal scaling.\n"
        "- Keep the same top-level sections unless a section heading can be made clearer.\n"
        "- Write for an engineer who is learning from the system interaction.\n"
        "- Explain which service or services are affected when the report names them.\n"
        "- Explain the recommended patterns as a systems and cloud architecture expert would: what problem they solve, how they change the architecture, why they help, and what tradeoffs they introduce.\n"
        "- Make the cause -> pattern -> tradeoff -> next step reasoning easy to follow.\n"
        "- Use clear markdown with short paragraphs and bullets, but do not make the explanation terse.\n\n"
        "Rewrite the following report as a clearer, more educational architecture explanation:\n\n"
        f"{base_report}"
    )


def _is_llm_output_consistent(state: GraphState, llm_output: str) -> bool:
    """
    Reject LLM text that contradicts empty/non-empty sections (e.g. claims “no smells”
    when smells exist). Keeps explanation faithful to upstream agents.
    """
    smells = state.get("smells", [])
    recommendations = state.get("recommendations", [])
    critiques = state.get("critiques", [])
    report = llm_output.lower()

    if smells and "no smells detected" in report:
        return False
    if critiques and "no constraints or warnings provided" in report:
        return False
    if critiques and "no constraint warnings were triggered" in report:
        return False
    if recommendations and "no architecture changes are currently recommended" in report:
        return False
    return True


_LLM_SYSTEM_INSTRUCTION = "You explain existing architecture outputs without changing the underlying facts."


def _normalize_llm_provider(provider: str) -> str:
    aliases = {
        "vertex_gemini": "agent_platform_gemini",
        "gcp_gemini": "agent_platform_gemini",
        "vertex_claude": "agent_platform_claude",
        "gcp_claude": "agent_platform_claude",
    }
    return aliases.get(provider, provider)


def _openai_compatible_report(state: GraphState, settings: Settings, provider: str) -> str | None:
    try:
        from openai import OpenAI  # type: ignore[reportMissingImports]
    except Exception:
        logger.warning("reasoning_agent openai sdk unavailable run_id=%s", state.get("run_id", "n/a"))
        return None

    if provider == "openai":
        if not settings.openai_api_key:
            logger.info(
                "reasoning_agent llm disabled run_id=%s provider=openai api_key_set=%s",
                state.get("run_id", "n/a"),
                bool(settings.openai_api_key),
            )
            return None
        client = OpenAI(api_key=settings.openai_api_key)
    else:
        # Ollama exposes an OpenAI-compatible API at /v1.
        # A placeholder key is accepted for local usage.
        client = OpenAI(base_url=settings.ollama_base_url, api_key="ollama")

    resp = client.chat.completions.create(
        model=settings.llm_model,
        temperature=0.2,
        messages=[
            {
                "role": "system",
                "content": _LLM_SYSTEM_INSTRUCTION,
            },
            {"role": "user", "content": _build_llm_prompt(state)},
        ],
    )
    return (resp.choices[0].message.content or "").strip() or None


def _resolve_gcp_project_id(settings: Settings) -> str | None:
    return (
        settings.gcp_project_id
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT_ID")
    )


def _gcp_project_id(state: GraphState, settings: Settings, provider: str) -> str | None:
    project_id = _resolve_gcp_project_id(settings)
    if project_id:
        return project_id
    logger.info(
        "reasoning_agent llm disabled run_id=%s provider=%s gcp_project_id_set=%s",
        state.get("run_id", "n/a"),
        provider,
        bool(project_id),
    )
    return None


def _gcp_location(settings: Settings) -> str:
    return settings.gcp_location or os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"


def _agent_platform_gemini_report(state: GraphState, settings: Settings, provider: str) -> str | None:
    project_id = _gcp_project_id(state, settings, provider)
    if not project_id:
        return None

    try:
        from google import genai  # type: ignore[reportMissingImports]
        from google.genai.types import HttpOptions  # type: ignore[reportMissingImports]
    except Exception:
        logger.warning("reasoning_agent google-genai sdk unavailable run_id=%s", state.get("run_id", "n/a"))
        return None

    # The current Agent Platform Gen AI SDK still names this endpoint selector
    # ``vertexai=True``. Keep that SDK detail internal and expose Agent Platform
    # provider names in ArchAgent config/docs.
    client = genai.Client(
        vertexai=True,
        project=project_id,
        location=_gcp_location(settings),
        http_options=HttpOptions(api_version=settings.gcp_genai_api_version),
    )
    try:
        resp = client.models.generate_content(
            model=settings.llm_model,
            contents=_build_llm_prompt(state),
            config={
                "temperature": 0.2,
                "system_instruction": _LLM_SYSTEM_INSTRUCTION,
            },
        )
        return (getattr(resp, "text", None) or "").strip() or None
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _extract_anthropic_text(message: Any) -> str:
    parts: List[str] = []
    for block in getattr(message, "content", []) or []:
        if isinstance(block, str):
            parts.append(block)
            continue
        if isinstance(block, dict):
            text = block.get("text")
        else:
            text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()


def _agent_platform_claude_report(state: GraphState, settings: Settings, provider: str) -> str | None:
    project_id = _gcp_project_id(state, settings, provider)
    if not project_id:
        return None

    try:
        from anthropic import AnthropicVertex  # type: ignore[reportMissingImports]
    except Exception:
        logger.warning("reasoning_agent anthropic agent platform sdk unavailable run_id=%s", state.get("run_id", "n/a"))
        return None

    client = AnthropicVertex(project_id=project_id, region=_gcp_location(settings))
    try:
        message = client.messages.create(
            model=settings.llm_model,
            max_tokens=3000,
            temperature=0.2,
            system=_LLM_SYSTEM_INSTRUCTION,
            messages=[{"role": "user", "content": _build_llm_prompt(state)}],
        )
        return _extract_anthropic_text(message) or None
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _llm_report(state: GraphState, settings: Settings) -> str | None:
    """
    Polish the report through the configured provider; return None on disabled
    config, missing SDK/credentials, or recoverable API errors.
    """
    if not settings.llm_reasoning_enabled:
        logger.info(
            "reasoning_agent llm disabled run_id=%s enabled=%s",
            state.get("run_id", "n/a"),
            settings.llm_reasoning_enabled,
        )
        return None

    provider = _normalize_llm_provider(settings.llm_provider)
    try:
        logger.info(
            "reasoning_agent llm config run_id=%s provider=%s model=%s base_url=%s gcp_location=%s gcp_project_id_set=%s gcp_genai_api_version=%s",
            state.get("run_id", "n/a"),
            provider,
            settings.llm_model,
            settings.ollama_base_url if provider == "ollama" else "n/a",
            _gcp_location(settings) if provider.startswith("agent_platform_") else "n/a",
            bool(_resolve_gcp_project_id(settings)) if provider.startswith("agent_platform_") else False,
            settings.gcp_genai_api_version if provider == "agent_platform_gemini" else "n/a",
        )

        if provider in {"openai", "ollama"}:
            content = _openai_compatible_report(state, settings, provider)
        elif provider == "agent_platform_gemini":
            content = _agent_platform_gemini_report(state, settings, provider)
        elif provider == "agent_platform_claude":
            content = _agent_platform_claude_report(state, settings, provider)
        else:
            logger.warning(
                "reasoning_agent unsupported llm provider run_id=%s provider=%s",
                state.get("run_id", "n/a"),
                settings.llm_provider,
            )
            return None

        if not content:
            logger.info(
                "reasoning_agent llm returned no content run_id=%s provider=%s model=%s",
                state.get("run_id", "n/a"),
                provider,
                settings.llm_model,
            )
            return None

        logger.info(
            "reasoning_agent llm success run_id=%s provider=%s model=%s report_chars=%d",
            state.get("run_id", "n/a"),
            provider,
            settings.llm_model,
            len(content),
        )
        return content
    except Exception as exc:
        # Avoid noisy tracebacks for expected API failures (quota, auth, rate limits).
        err_name = exc.__class__.__name__
        logger.warning(
            "reasoning_agent llm call failed run_id=%s provider=%s error_type=%s message=%s",
            state.get("run_id", "n/a"),
            provider,
            err_name,
            str(exc),
        )
        return None


def reasoning_node(state: GraphState, settings: Settings | None = None) -> GraphState:
    """Attach ``explanation_report``; prefer LLM polish when safe, else deterministic."""
    run_id = state.get("run_id", "n/a")
    logger.info(
        "reasoning_agent start run_id=%s smells=%d recommendations=%d critiques=%d",
        run_id,
        len(state.get("smells", [])),
        len(state.get("recommendations", [])),
        len(state.get("critiques", [])),
    )
    deterministic_report = build_explanation_report(state)
    if settings is not None:
        llm_output = _llm_report(state, settings)
        if llm_output:
            if _is_llm_output_consistent(state, llm_output):
                state["explanation_report"] = llm_output
                logger.info("reasoning_agent done run_id=%s source=llm", run_id)
                return state
            logger.warning(
                "reasoning_agent llm output inconsistent run_id=%s source=fallback_deterministic",
                run_id,
            )
    state["explanation_report"] = deterministic_report
    logger.info(
        "reasoning_agent done run_id=%s source=deterministic_fallback report_chars=%d",
        run_id,
        len(state.get("explanation_report", "")),
    )
    return state
