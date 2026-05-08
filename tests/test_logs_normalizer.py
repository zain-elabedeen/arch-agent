"""Source-neutral log normalizer tests."""

from agent.app.connectors.logs.models import RawLogBatch
from agent.app.connectors.logs.normalizer import normalize_logs, normalize_log_line


def _raw_log(lines):
    return RawLogBatch(
        source="kubernetes",
        service="test-api",
        namespace="default",
        resource="test-api-abc-123",
        container="app",
        lines=lines,
    )


def test_json_logs_extract_latency_status_error_and_percentiles():
    lines = []
    for idx in range(1, 21):
        status = 503 if idx == 20 else 200
        level = "error" if status >= 500 else "info"
        lines.append(
            (
                "2026-05-07T10:00:00Z "
                f'{{"level":"{level}","message":"request finished","method":"GET",'
                f'"route":"/orders","status_code":{status},"duration_ms":{idx * 10},'
                '"trace_id":"abc"}'
            )
        )

    out = normalize_logs([_raw_log(lines)])

    assert len(out["events"]) == 20
    assert out["signals"]["request_count"] == 20.0
    assert out["signals"]["error_count"] == 1.0
    assert out["signals"]["status_5xx_rate"] == 0.05
    assert out["signals"]["request_latency_p90_ms"] == 180.0
    assert out["signals"]["request_latency_p95_ms"] == 190.0
    assert out["data_quality"]["latency_percentiles_reliable"] is True
    assert out["service_signals"]["test-api"]["request_count"] == 20.0


def test_plain_text_timeout_log_is_categorized():
    event, failures = normalize_log_line(
        _raw_log([]),
        "2026-05-07T10:00:00Z upstream request timed out status=504 latency=742ms",
    )

    assert failures == 0
    assert event is not None
    assert event.category == "timeout"
    assert event.status_code == 504
    assert event.latency_ms == 742.0
    assert event.is_error is True


def test_low_latency_sample_count_does_not_emit_percentiles():
    out = normalize_logs(
        [
            _raw_log(
                [
                    '2026-05-07T10:00:00Z {"level":"info","message":"ok","latency_ms":10,"status_code":200}',
                    '2026-05-07T10:00:01Z {"level":"info","message":"ok","latency_ms":20,"status_code":200}',
                ]
            )
        ]
    )

    assert "request_latency_p95_ms" not in out["signals"]
    assert out["data_quality"]["latency_sample_count"] == 2
    assert out["data_quality"]["latency_percentiles_reliable"] is False
