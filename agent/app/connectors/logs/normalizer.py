"""
Normalize raw logs into canonical log events and aggregate signals.

Structured JSON is preferred. Plain text falls back to conservative regex and
keyword parsing so logs can still contribute deterministic evidence without
turning ArchAgent into a log-search product.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from agent.app.connectors.logs.models import RawLogBatch
from agent.app.state import LogEvent, LogSignals, SnapshotLogDataQuality, SnapshotLogs

MIN_LATENCY_SAMPLES_FOR_PERCENTILES = 20
_MAX_MESSAGE_SAMPLE_CHARS = 300

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T[^\s]+)\s+(.*)$")
_STATUS_RE = re.compile(r"\b(?:status(?:_code)?|code)[=:\s]+([1-5]\d{2})\b", re.IGNORECASE)
_HTTP_STATUS_RE = re.compile(r"\bHTTP/[0-9.]+\"\s+([1-5]\d{2})\b")
_LATENCY_RE = re.compile(
    r"\b(?:latency|duration|elapsed|response_time)(?:_ms)?[=:\s]+([0-9]+(?:\.[0-9]+)?)\s*(ms|s)?\b",
    re.IGNORECASE,
)
_TOOK_RE = re.compile(r"\btook\s+([0-9]+(?:\.[0-9]+)?)\s*(ms|s)\b", re.IGNORECASE)
_METHOD_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[^\s\"?]+)", re.IGNORECASE)

_LATENCY_ALIASES = ("latency_ms", "duration_ms", "elapsed_ms", "response_time_ms")
_LEVEL_ALIASES = ("level", "severity", "logger.level")
_MESSAGE_ALIASES = ("message", "msg", "log", "error", "exception")
_TRACE_ALIASES = ("trace_id", "traceId", "trace.id")
_ROUTE_ALIASES = ("route", "path", "request_path", "http.route", "url.path")
_METHOD_ALIASES = ("method", "http.method", "request_method")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _split_timestamp(line: str) -> Tuple[str, str]:
    m = _TS_RE.match(line.strip())
    if not m:
        return _utc_now(), line.strip()
    return m.group(1), m.group(2).strip()


def _nested_get(data: Dict[str, Any], dotted: str) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _first(data: Dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = _nested_get(data, key) if "." in key else data.get(key)
        if value is not None:
            return value
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _message_sample(value: Any) -> str:
    text = str(value or "").strip().replace("\n", " ")
    return text[:_MAX_MESSAGE_SAMPLE_CHARS]


def _normalize_level(level: Any, status_code: Optional[int], category: str) -> str:
    raw = str(level or "").strip().lower()
    if raw:
        return raw
    if status_code and status_code >= 500:
        return "error"
    if category in {"timeout", "dependency_error", "probe_failure", "crash_signal"}:
        return "error"
    return "info"


def _category(message: str, status_code: Optional[int]) -> Tuple[str, Optional[str]]:
    text = message.lower()
    if "timeout" in text or "timed out" in text or "deadline exceeded" in text:
        return "timeout", "timeout"
    if (
        "connection refused" in text
        or "connection reset" in text
        or "upstream unavailable" in text
        or "dependency unavailable" in text
        or "name resolution" in text
        or "dns" in text
        or "no such host" in text
    ):
        return "dependency_error", "dependency_error"
    if "readiness probe" in text or "liveness probe" in text or "probe failed" in text:
        return "probe_failure", "probe_failure"
    if "oomkilled" in text or "oom killed" in text or "out of memory" in text:
        return "crash_signal", "oom_killed"
    if "crashloopbackoff" in text or "crash loop" in text or "panic" in text or "traceback" in text:
        return "crash_signal", "crash"
    if status_code is not None and status_code >= 500:
        return "http_5xx", None
    if status_code is not None and status_code >= 400:
        return "http_4xx", None
    return ("request" if status_code is not None else "uncategorized"), None


def _plain_status(message: str) -> Optional[int]:
    for regex in (_STATUS_RE, _HTTP_STATUS_RE):
        m = regex.search(message)
        if m:
            return _to_int(m.group(1))
    return None


def _plain_latency(message: str) -> Optional[float]:
    for regex in (_LATENCY_RE, _TOOK_RE):
        m = regex.search(message)
        if not m:
            continue
        value = _to_float(m.group(1))
        if value is None:
            return None
        unit = (m.group(2) or "ms").lower()
        return value * 1000.0 if unit == "s" else value
    return None


def _plain_method_route(message: str) -> Tuple[Optional[str], Optional[str]]:
    m = _METHOD_RE.search(message)
    if not m:
        return None, None
    return m.group(1).upper(), m.group(2)


def _parse_json_log(payload: str) -> Tuple[Dict[str, Any] | None, int]:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None, 0
    return parsed if isinstance(parsed, dict) else None, 0


def _event_from_json(batch: RawLogBatch, timestamp: str, data: Dict[str, Any], raw_payload: str) -> LogEvent:
    message = _message_sample(_first(data, _MESSAGE_ALIASES) or raw_payload)
    latency_ms = _to_float(_first(data, _LATENCY_ALIASES))
    status_code = _to_int(_first(data, ("status_code", "http.status_code", "status")))
    method = _first(data, _METHOD_ALIASES)
    route = _first(data, _ROUTE_ALIASES)
    error_type = _first(data, ("error_type", "exception.type", "error.kind"))
    category, category_error_type = _category(message, status_code)
    error_type = str(error_type or category_error_type) if (error_type or category_error_type) else None
    level = _normalize_level(_first(data, _LEVEL_ALIASES), status_code, category)
    is_error = level in {"error", "critical", "fatal"} or bool(status_code and status_code >= 500) or bool(error_type)

    return LogEvent(
        timestamp=str(_first(data, ("timestamp", "time", "@timestamp")) or timestamp),
        service=str(_first(data, ("service", "service_name", "app")) or batch.service),
        namespace=batch.namespace,
        pod=batch.resource,
        container=batch.container,
        level=level,
        category=category,
        message_sample=message,
        method=str(method).upper() if method else None,
        route=str(route) if route else None,
        status_code=status_code,
        latency_ms=latency_ms,
        is_error=is_error,
        error_type=error_type,
        trace_id=str(_first(data, _TRACE_ALIASES)) if _first(data, _TRACE_ALIASES) else None,
    )


def _event_from_plain_text(batch: RawLogBatch, timestamp: str, payload: str) -> LogEvent:
    message = _message_sample(payload)
    status_code = _plain_status(message)
    latency_ms = _plain_latency(message)
    method, route = _plain_method_route(message)
    category, error_type = _category(message, status_code)
    level = _normalize_level(None, status_code, category)
    is_error = level in {"error", "critical", "fatal"} or bool(status_code and status_code >= 500) or bool(error_type)
    return LogEvent(
        timestamp=timestamp,
        service=batch.service,
        namespace=batch.namespace,
        pod=batch.resource,
        container=batch.container,
        level=level,
        category=category,
        message_sample=message,
        method=method,
        route=route,
        status_code=status_code,
        latency_ms=latency_ms,
        is_error=is_error,
        error_type=error_type,
    )


def normalize_log_line(batch: RawLogBatch, line: str) -> Tuple[LogEvent | None, int]:
    """
    Convert one raw line to a canonical event.

    Returns ``(event, parse_failures)``; uncategorized info/debug noise returns
    ``(None, 0)`` so it does not inflate stored evidence.
    """
    timestamp, payload = _split_timestamp(line)
    parsed, failures = _parse_json_log(payload)
    if parsed is not None:
        event = _event_from_json(batch, timestamp, parsed, payload)
    else:
        event = _event_from_plain_text(batch, timestamp, payload)
        if payload.lstrip().startswith(("{", "[")):
            failures += 1

    if event.category == "uncategorized" and event.level in {"debug", "info"} and event.latency_ms is None:
        return None, failures
    return event, failures


def _is_request_event(event: LogEvent) -> bool:
    return event.status_code is not None or event.latency_ms is not None or event.method is not None or event.route is not None


def _percentile(latencies: List[float], q: float) -> float | None:
    if len(latencies) < MIN_LATENCY_SAMPLES_FOR_PERCENTILES:
        return None
    ordered = sorted(latencies)
    rank = max(0, math.ceil(q * len(ordered)) - 1)
    return float(ordered[min(rank, len(ordered) - 1)])


def _aggregate(events: List[LogEvent]) -> LogSignals:
    request_count = sum(1 for e in events if _is_request_event(e))
    status_4xx = sum(1 for e in events if e.status_code is not None and 400 <= e.status_code < 500)
    status_5xx = sum(1 for e in events if e.status_code is not None and e.status_code >= 500)
    error_count = sum(1 for e in events if e.is_error)
    latencies = [float(e.latency_ms) for e in events if e.latency_ms is not None]
    denom = float(request_count) if request_count else 0.0

    return LogSignals(
        request_count=float(request_count),
        error_count=float(error_count),
        error_rate=(float(error_count) / denom) if denom else None,
        status_5xx_rate=(float(status_5xx) / denom) if denom else None,
        status_4xx_rate=(float(status_4xx) / denom) if denom else None,
        request_latency_p50_ms=_percentile(latencies, 0.50),
        request_latency_p90_ms=_percentile(latencies, 0.90),
        request_latency_p95_ms=_percentile(latencies, 0.95),
        request_latency_p99_ms=_percentile(latencies, 0.99),
        timeout_count=float(sum(1 for e in events if e.category == "timeout")),
        dependency_error_count=float(sum(1 for e in events if e.category == "dependency_error")),
        probe_failure_count=float(sum(1 for e in events if e.category == "probe_failure")),
        oom_killed_count=float(sum(1 for e in events if e.error_type == "oom_killed")),
        crash_signal_count=float(sum(1 for e in events if e.category == "crash_signal")),
    )


def normalize_logs(
    batches: Iterable[RawLogBatch],
    *,
    logs_enabled: bool = True,
) -> Dict[str, Any]:
    """Normalize raw logs into ``SnapshotLogs`` and return a JSON-ready dict."""
    raw_batches = list(batches)
    events: List[LogEvent] = []
    parse_failures = 0
    resources_with_logs = 0
    resources_without_logs = 0

    for batch in raw_batches:
        if batch.lines:
            resources_with_logs += 1
        else:
            resources_without_logs += 1
        for line in batch.lines:
            event, failures = normalize_log_line(batch, line)
            parse_failures += failures
            if event is not None:
                events.append(event)

    by_service: Dict[str, List[LogEvent]] = defaultdict(list)
    for event in events:
        by_service[event.service].append(event)

    global_signals = _aggregate(events)
    service_signals = {service: _aggregate(items) for service, items in sorted(by_service.items())}
    latency_sample_count = sum(1 for e in events if e.latency_ms is not None)
    data_quality = SnapshotLogDataQuality(
        logs_enabled=logs_enabled,
        pods_with_logs=resources_with_logs,
        pods_without_logs=resources_without_logs,
        parse_failures=parse_failures,
        latency_sample_count=latency_sample_count,
        latency_percentiles_reliable=latency_sample_count >= MIN_LATENCY_SAMPLES_FOR_PERCENTILES,
    )
    snapshot_logs = SnapshotLogs(
        events=events,
        signals=global_signals,
        service_signals=service_signals,
        data_quality=data_quality,
    )
    return snapshot_logs.model_dump(exclude_none=True)
