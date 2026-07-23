from agent.app.config import Settings
from agent.app.nodes.log_analysis import _llm_failure_payload, _parse_json_object, classify_log_samples, log_analysis_node
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
    assert classify_log_samples([_event()], Settings(log_llm_enabled=False)) == {"disabled_reason": "log_llm_disabled"}


def test_log_analysis_agent_reports_no_logs_present():
    assert classify_log_samples([], Settings(log_llm_enabled=True)) == {
        "status": "no_logs_present",
        "message": "No normalized log events were available for this run.",
        "event_count": 0,
        "sample_count": 0,
    }


def test_log_analysis_agent_sends_info_logs_when_they_are_present(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT_ID", raising=False)
    event = _event().model_copy(update={"level": "info", "category": "request", "is_error": False})
    assert classify_log_samples([event], Settings(log_llm_enabled=True, gcp_project_id="")) == {
        "disabled_reason": "gcp_project_id_missing"
    }


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


def test_log_analysis_quota_errors_are_explicit():
    error = RuntimeError("429 RESOURCE_EXHAUSTED. Resource exhausted. Please try again later.")
    assert _llm_failure_payload(error, 133, 20)["ignored_reason"] == "llm_quota_exhausted"


def test_log_analysis_agent_returns_gemini_analysis(monkeypatch):
    from google import genai

    class FakeResponse:
        text = (
            '{"category": "timeout", "suspected_component": "test-api", '
            '"confidence": 0.9, "summary": "upstream timeout", "evidence_terms": ["timeout"]}'
        )

    class FakeModels:
        def generate_content(self, **kwargs):
            assert kwargs["model"] == "gemini-test"
            return FakeResponse()

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["vertexai"] is True
            assert kwargs["project"] == "test-project"
            assert kwargs["location"] == "global"
            self.models = FakeModels()

        def close(self):
            pass

    monkeypatch.setattr(genai, "Client", FakeClient)

    out = classify_log_samples(
        [_event()],
        Settings(
            log_llm_enabled=True,
            llm_provider="agent_platform_gemini",
            llm_model="gemini-test",
            gcp_project_id="test-project",
        ),
    )

    assert out == {
        "category": "timeout",
        "suspected_component": "test-api",
        "confidence": 0.9,
        "summary": "upstream timeout",
        "evidence_terms": ["timeout"],
        "event_count": 1,
        "sample_count": 1,
        "analysis_source": "gemini",
        "llm_model": "gemini-test",
    }


def test_log_analysis_parser_accepts_fenced_json():
    parsed = _parse_json_object("```json\n{\"category\": \"timeout\", \"ignored\": true}\n```")
    assert parsed == {"category": "timeout"}
