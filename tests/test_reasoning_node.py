from agent.app.nodes.reasoning import (
    _build_llm_prompt,
    _is_llm_output_consistent,
    build_explanation_report,
    reasoning_node,
)
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


def test_llm_prompt_rewrites_deterministic_report_instead_of_raw_payload():
    state = {
        "smells": [{"type": "cpu_saturation", "severity": "high", "confidence": 0.91}],
        "recommendations": [],
        "critiques": [],
    }
    prompt = _build_llm_prompt(state)

    assert "Rewrite the following report" in prompt
    assert "## Runtime Architecture Report" in prompt
    assert '"smells"' not in prompt


def test_llm_output_consistency_rejects_false_no_smells_claim():
    state = {
        "smells": [{"type": "cpu_saturation", "severity": "high", "confidence": 0.91}],
        "recommendations": [],
        "critiques": [],
    }
    llm_output = "## Runtime Architecture Report\n\nNo smells detected."
    assert _is_llm_output_consistent(state, llm_output) is False
