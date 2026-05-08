from agent.app.config import Settings
from agent.app.nodes.log_analysis import classify_log_samples, log_analysis_node
from agent.app.state import LogEvent


def _event() -> LogEvent:
    return LogEvent(
        timestamp="2026-05-07T10:00:00Z",
        service="test-api",
        namespace="default",
        pod="test-api-abc-123",
        level="error",
        category="timeout",
        message_sample="upstream request timed out",
        is_error=True,
    )


def test_log_analysis_agent_is_disabled_by_default():
    assert classify_log_samples([_event()], Settings(log_llm_enabled=False)) == {}


def test_log_analysis_agent_requires_gcp_project_when_enabled(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT_ID", raising=False)

    out = classify_log_samples(
        [_event()],
        Settings(
            log_llm_enabled=True,
            llm_provider="agent_platform_gemini",
            gcp_project_id=None,
        ),
    )

    assert out == {"disabled_reason": "gcp_project_id_missing"}


def test_log_analysis_node_reads_raw_logs_without_changing_decision_state(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT_ID", raising=False)
    state = {
        "run_id": "test-run",
        "raw_logs": {"events": [_event().model_dump()]},
        "smells": [{"type": "timeout_pressure"}],
        "recommendations": [],
    }

    out = log_analysis_node(
        state,
        Settings(
            log_llm_enabled=True,
            llm_provider="agent_platform_gemini",
            gcp_project_id=None,
        ),
    )

    assert out["log_analysis"] == {"disabled_reason": "gcp_project_id_missing"}
    assert out["smells"] == [{"type": "timeout_pressure"}]
    assert out["recommendations"] == []
