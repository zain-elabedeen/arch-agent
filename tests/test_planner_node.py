from agent.app.nodes.planner import build_plan, build_scoped_plan
from agent.app.state import AnalysisScope, Recommendation


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


def test_scoped_plan_resets_step_numbers_per_scope():
    api_scope = AnalysisScope(
        kind="workload",
        id="k8s:default:workload:api",
        name="api",
        label="api / default",
        namespace="default",
        node_id="k8s:default:workload:api",
    )
    worker_scope = AnalysisScope(
        kind="workload",
        id="k8s:default:workload:worker",
        name="worker",
        label="worker / default",
        namespace="default",
        node_id="k8s:default:workload:worker",
    )

    plan = build_scoped_plan(
        [
            Recommendation(
                id="api:horizontal_scaling",
                pattern="horizontal_scaling",
                solution="Add more instances",
                impact="high",
                effort="medium",
                priority=1,
                reason="Remove single-replica risk",
                scope=api_scope,
            ),
            Recommendation(
                id="worker:horizontal_scaling",
                pattern="horizontal_scaling",
                solution="Add more instances",
                impact="high",
                effort="medium",
                priority=1,
                reason="Remove single-replica risk",
                scope=worker_scope,
            ),
        ]
    )

    assert [step.title for step in plan] == ["Step 1: Apply horizontal_scaling", "Step 1: Apply horizontal_scaling"]
    assert [step.scope.node_id for step in plan] == [
        "k8s:default:workload:api",
        "k8s:default:workload:worker",
    ]
    assert [step.recommendation_id for step in plan] == ["api:horizontal_scaling", "worker:horizontal_scaling"]
