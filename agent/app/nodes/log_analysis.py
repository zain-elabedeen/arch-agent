"""
Experimental log-analysis agent.

This node may use Gemini to classify sampled normalized log events, but it never
creates smells, recommendations, critiques, or plan steps. Deterministic log
signals remain the source of truth for architecture decisions.
"""

from __future__ import annotations

import json
import os
import re
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
    """Sample all available log events, prioritizing errors but not requiring them."""
    event_list = list(events)

    def priority(event: LogEvent) -> tuple[int, str]:
        level = (event.level or "").lower()
        high_signal = (
            event.is_error
            or level in {"warning", "warn", "error", "critical", "fatal"}
            or event.category in {"timeout", "dependency_error", "probe_failure", "crash_signal", "http_5xx"}
            or bool(event.status_code and event.status_code >= 400)
        )
        return (0 if high_signal else 1, event.timestamp)

    payload: List[Dict[str, Any]] = []
    for event in sorted(event_list, key=priority):
        payload.append(
            {
                "timestamp": event.timestamp,
                "service": event.service,
                "namespace": event.namespace,
                "pod": event.pod,
                "level": event.level,
                "category": event.category,
                "message_sample": event.message_sample,
                "status_code": event.status_code,
                "latency_ms": event.latency_ms,
                "error_type": event.error_type,
                "count": event.count,
            }
        )
        if len(payload) >= sample_limit:
            break
    return payload


def _parse_json_object(text: str) -> Dict[str, Any]:
    raw = text.strip()
    candidates = [raw]
    unfenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()
    if unfenced != raw:
        candidates.append(unfenced)
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start : end + 1])

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if not isinstance(parsed, dict):
            raise ValueError("log analysis agent returned non-object JSON")
        allowed = {"category", "suspected_component", "confidence", "summary", "evidence_terms"}
        return {k: v for k, v in parsed.items() if k in allowed}
    raise ValueError(f"log analysis agent returned invalid JSON: {last_error}")


def _log_llm_model(settings: Settings) -> str:
    return settings.log_llm_model or settings.llm_model


def _llm_failure_payload(exc: Exception, event_count: int, sample_count: int) -> Dict[str, Any]:
    message = str(exc)
    quota_exhausted = "RESOURCE_EXHAUSTED" in message or "429" in message
    return {
        "ignored_reason": "llm_quota_exhausted" if quota_exhausted else "invalid_or_failed_llm_output",
        "error_type": exc.__class__.__name__,
        "message": message,
        "event_count": event_count,
        "sample_count": sample_count,
    }


def classify_log_samples(events: Iterable[LogEvent], settings: Settings) -> Dict[str, Any]:
    """Return structured Gemini analysis or explicit empty-state metadata."""
    if not settings.log_llm_enabled:
        return {"disabled_reason": "log_llm_disabled"}

    event_list = list(events)
    if not event_list:
        return {
            "status": "no_logs_present",
            "message": "No normalized log events were available for this run.",
            "event_count": 0,
            "sample_count": 0,
        }

    samples = _event_payload(event_list, max(1, settings.log_sample_limit))
    if not samples:
        return {
            "status": "no_log_samples",
            "message": "Log events were present, but no samples could be prepared for LLM classification.",
            "event_count": len(event_list),
            "sample_count": 0,
        }

    provider = _normalize_provider(settings.llm_provider)
    if provider != "agent_platform_gemini":
        return {"disabled_reason": f"provider_{settings.llm_provider}_not_supported_for_log_analysis"}

    project_id = _resolve_project_id(settings)
    if not project_id:
        return {"disabled_reason": "gcp_project_id_missing"}

    try:
        from google import genai  # type: ignore[reportMissingImports]
        from google.genai.types import HttpOptions  # type: ignore[reportMissingImports]
    except Exception as exc:
        logger.warning(
            "log_analysis_agent unavailable: google-genai sdk import failed error_type=%s message=%s",
            exc.__class__.__name__,
            exc,
        )
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
                model=_log_llm_model(settings),
                contents=prompt,
                config={
                    "temperature": 0.0,
                    "system_instruction": _SYSTEM,
                    "response_mime_type": "application/json",
                    "max_output_tokens": settings.log_llm_max_output_tokens,
                },
            )
            content = getattr(resp, "text", "") or ""
            analysis = _parse_json_object(content.strip())
            analysis.setdefault("event_count", len(event_list))
            analysis.setdefault("sample_count", len(samples))
            analysis.setdefault("analysis_source", "gemini")
            analysis.setdefault("llm_model", _log_llm_model(settings))
            return analysis
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()
    except Exception as e:
        logger.warning("log_analysis_agent failed error=%s", e)
        return _llm_failure_payload(e, len(event_list), len(samples))


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
