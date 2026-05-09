from agent.app.services.analysis_scoping import build_scoped_analysis, scope_smells
from agent.app.state import AnalysisScope, Recommendation


def _topology():
    return {
        "services": ["api", "worker"],
        "service_details": {
            "api": {"namespace": "default", "replicas": 1},
            "worker": {"namespace": "default", "replicas": 1},
        },
        "graph": {
            "nodes": [
                {
                    "id": "k8s:default:workload:api",
                    "name": "api",
                    "namespace": "default",
                    "is_external": False,
                },
                {
                    "id": "k8s:default:workload:worker",
                    "name": "worker",
                    "namespace": "default",
                    "is_external": False,
                },
            ],
            "edges": [],
            "meta": {},
        },
    }


def test_multi_service_smell_splits_into_workload_scopes():
    smells = scope_smells(
        [
            {
                "type": "single_instance_risk",
                "severity": "medium",
                "confidence": 0.74,
                "evidence": {"services": "api, worker", "single_instance_service_count": 2.0},
            }
        ],
        _topology(),
    )

    assert len(smells) == 2
    assert {smell["scope"]["node_id"] for smell in smells} == {
        "k8s:default:workload:api",
        "k8s:default:workload:worker",
    }
    assert {smell["evidence"]["services"] for smell in smells} == {"api", "worker"}
    assert all(smell["evidence"]["single_instance_service_count"] == 1.0 for smell in smells)


def test_ambiguous_service_name_falls_back_to_cluster_scope():
    topology = {
        "services": ["api"],
        "graph": {
            "nodes": [
                {"id": "k8s:a:workload:api", "name": "api", "namespace": "a", "is_external": False},
                {"id": "k8s:b:workload:api", "name": "api", "namespace": "b", "is_external": False},
            ],
            "edges": [],
            "meta": {},
        },
    }

    smells = scope_smells(
        [
            {
                "type": "cpu_saturation",
                "severity": "high",
                "confidence": 0.88,
                "evidence": {"services": "api", "cpu": 0.95},
            }
        ],
        topology,
    )

    assert len(smells) == 1
    assert smells[0]["scope"]["kind"] == "cluster"
    assert smells[0]["scope"]["id"] == "cluster"
    assert smells[0]["evidence"]["unresolved_services"] == "api"


def test_scoped_analysis_groups_pipeline_outputs_by_scope():
    scope = AnalysisScope(
        kind="workload",
        id="k8s:default:workload:api",
        name="api",
        label="api / default",
        namespace="default",
        node_id="k8s:default:workload:api",
    )
    grouped = build_scoped_analysis(
        [{"id": "smell-1", "type": "single_instance_risk", "scope": scope.model_dump()}],
        [
            Recommendation(
                id="rec-1",
                pattern="horizontal_scaling",
                solution="Add more instances",
                impact="high",
                effort="medium",
                priority=1,
                reason="Remove risk",
                scope=scope,
                source_smells=["smell-1"],
            )
        ],
        [],
        [],
    )

    assert len(grouped) == 1
    assert grouped[0].scope.node_id == "k8s:default:workload:api"
    assert grouped[0].smells[0].id == "smell-1"
    assert grouped[0].recommendations[0].source_smells == ["smell-1"]
