from uuid import uuid4

from agent.app.config import Settings
from agent.app.connectors.orchestrator import _connector_names, _run_connector


def test_orchestrator_connector_names_follow_settings():
    settings = Settings(ingestion_connectors="kubernetes, logs", logs_enabled=True)
    assert _connector_names(settings) == ["kubernetes", "logs"]

    settings = Settings(ingestion_connectors="kubernetes,logs", logs_enabled=False)
    assert _connector_names(settings) == ["kubernetes"]


def test_orchestrator_passes_shared_run_id_to_connection_workers(monkeypatch):
    run_id = uuid4()
    calls = []

    def fake_kubernetes_worker(**kwargs):
        calls.append(("kubernetes", kwargs["run_id"], kwargs["apis"]))

    def fake_logs_worker(**kwargs):
        calls.append(("logs", kwargs["run_id"], kwargs["apis"]))

    monkeypatch.setattr("agent.app.connectors.orchestrator.run_kubernetes_ingestion_once", fake_kubernetes_worker)
    monkeypatch.setattr("agent.app.connectors.orchestrator.run_logs_ingestion_once", fake_logs_worker)

    _run_connector("kubernetes", run_id, apis="k8s-apis")
    _run_connector("logs", run_id, apis="k8s-apis")

    assert calls == [
        ("kubernetes", run_id, "k8s-apis"),
        ("logs", run_id, "k8s-apis"),
    ]
