from agent.app.nodes.planner import build_plan
from agent.app.state import Recommendation


def test_planner_prioritizes_high_impact_low_effort_first_then_medium_medium():
    recommendations = [
        Recommendation(
            pattern="service_decomposition",
            solution="Split service by bounded context",
            impact="high",
            effort="high",
            priority=1,
            reason="Reduce coupling",
        ),
        Recommendation(
            pattern="load_balancing",
            solution="Distribute traffic across instances",
            impact="high",
            effort="low",
            priority=2,
            reason="Balance load",
        ),
        Recommendation(
            pattern="api_gateway",
            solution="Introduce edge gateway",
            impact="medium",
            effort="medium",
            priority=3,
            reason="Centralize cross-cutting concerns",
        ),
    ]

    plan = build_plan(recommendations)

    assert plan[0].title.lower().endswith("load_balancing")
    assert plan[1].title.lower().endswith("api_gateway")
    assert plan[2].title.lower().endswith("service_decomposition")
