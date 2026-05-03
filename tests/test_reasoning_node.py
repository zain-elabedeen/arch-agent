from agent.app.nodes.reasoning import build_explanation_report, reasoning_node
from agent.app.state import Recommendation, Critique


def test_reasoning_report_summarizes_existing_outputs_only():
    state = {
        "smells": [
            {"type": "cpu_saturation", "severity": "high", "confidence": 0.88},
            {"type": "queue_backlog", "severity": "medium", "confidence": 0.87},
        ],
        "recommendations": [
            Recommendation(
                pattern="horizontal_scaling",
                solution="Add more instances",
                impact="high",
                effort="medium",
                priority=1,
                reason="Increase compute capacity",
            )
        ],
        "critiques": [
            Critique(
                pattern_id="horizontal_scaling",
                level="warning",
                message="Resources are already saturated.",
            )
        ],
        "final_plan": [],
    }

    out = reasoning_node(state)
    report = out["explanation_report"]

    assert "Detected Smells" in report
    assert "Recommended Architecture Moves" in report
    assert "Constraints and Warnings" in report
    assert "cpu_saturation" in report
    assert "horizontal_scaling" in report


def test_build_explanation_handles_empty_sections():
    state = {"smells": [], "recommendations": [], "critiques": []}
    report = build_explanation_report(state)
    assert "No architecture smells were detected" in report
    assert "No architecture changes are currently recommended" in report
