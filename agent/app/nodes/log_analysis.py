"""
Experimental log-analysis agent.

This node may use Gemini to classify sampled normalized log events, but it never
creates smells, recommendations, critiques, or plan steps. Deterministic log
signals remain the source of truth for architecture decisions.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List

from agent.app.config import Settings
from agent.app.logging_utils import get_logger
from agent.app.state import GraphState, LogEvent

logger = get_logger("agent.nodes.log_analysis")

_SYSTEM = (
    "Classify infrastructure log samples. Return strict JSON only with keys: "
    "category, suspected_component, confidence, summary, evidence_terms. "
    "Do not invent services, metrics, recommendations, or actions."
)


def _normalize_provider(provider: str) -> str:
    aliases = {
        "vertex_gemini": "agent_platform_gemini",
        "gcp_gemini": "agent_platform_gemini",
    }
    return aliases.get(provider, provider)


def _resolve_project_id(settings: Settings) -> str | None:
    return (
        settings.gcp_project_id
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT_ID")
    )


def _resolve_location(settings: Settings) -> str:
    return settings.gcp_location or os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"


def _event_payload(events: Iterable[LogEvent], sample_limit: int) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for event in events:
        if event.level not in {"warning", "warn", "error", "critical", "fatal"} and not event.is_error:
            continue
        payload.append(
            {
                "service": event.service,
                "level": event.level,
                "category": event.category,
                "message_sample": event.message_sample,
                "status_code": event.status_code,
                "latency_ms": event.latency_ms,
            }
        )
        if len(payload) >= sample_limit:
            break
    return payload


def _parse_json_object(text: str) -> Dict[str, Any]:
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("log analysis agent returned non-object JSON")
    allowed = {"category", "suspected_component", "confidence", "summary", "evidence_terms"}
    return {k: v for k, v in parsed.items() if k in allowed}


def classify_log_samples(events: Iterable[LogEvent], settings: Settings) -> Dict[str, Any]:
    """Return structured Gemini analysis or disabled metadata."""
    if not settings.log_llm_enabled:
        return {}

    samples = _event_payload(events, max(1, settings.log_sample_limit))
    if not samples:
        return {}

    provider = _normalize_provider(settings.llm_provider)
    if provider != "agent_platform_gemini":
        return {"disabled_reason": f"provider_{settings.llm_provider}_not_supported_for_log_analysis"}

    project_id = _resolve_project_id(settings)
    if not project_id:
        return {"disabled_reason": "gcp_project_id_missing"}

    try:
        from google import genai  # type: ignore[reportMissingImports]
        from google.genai.types import HttpOptions  # type: ignore[reportMissingImports]
    except Exception:
        logger.warning("log_analysis_agent unavailable: google-genai sdk missing")
        return {"disabled_reason": "google_genai_sdk_missing"}

    prompt = (
        "Classify these log samples into one dominant infrastructure category. "
        "Return JSON with category, suspected_component, confidence, summary, evidence_terms.\n\n"
        f"{json.dumps(samples, ensure_ascii=True)}"
    )
    try:
        client = genai.Client(
            vertexai=True,
            project=project_id,
            location=_resolve_location(settings),
            http_options=HttpOptions(api_version=settings.gcp_genai_api_version),
        )
        try:
            resp = client.models.generate_content(
                model=settings.llm_model,
                contents=prompt,
                config={
                    "temperature": 0.0,
                    "system_instruction": _SYSTEM,
                    "response_mime_type": "application/json",
                },
            )
            content = getattr(resp, "text", "") or ""
            return _parse_json_object(content.strip())
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception as e:
        logger.warning("log_analysis_agent failed error=%s", e)
        return {"ignored_reason": "invalid_or_failed_llm_output"}


def _events_from_state(state: GraphState) -> List[LogEvent]:
    raw_logs = state.get("raw_logs") or {}
    raw_events = raw_logs.get("events") if isinstance(raw_logs, dict) else []
    events: List[LogEvent] = []
    for item in raw_events or []:
        try:
            events.append(LogEvent.model_validate(item))
        except Exception:
            continue
    return events


def log_analysis_node(state: GraphState, settings: Settings) -> GraphState:
    """
    Optional log-analysis agent.

    Output is stored under ``state["log_analysis"]`` for reporting context only.
    """
    run_id = state.get("run_id", "n/a")
    events = _events_from_state(state)
    logger.info(
        "log_analysis_agent start run_id=%s enabled=%s events=%d",
        run_id,
        settings.log_llm_enabled,
        len(events),
    )
    analysis = classify_log_samples(events, settings)
    state["log_analysis"] = analysis
    logger.info(
        "log_analysis_agent done run_id=%s analysis_keys=%s",
        run_id,
        sorted(analysis.keys()),
    )
    return state
