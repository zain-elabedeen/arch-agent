from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from agent.app.config import Settings
from agent.app.product.store import ProductStore
from agent.app.product.tasks import CloudTasksDispatcher


def _request(token="token"):
    return Request({"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})


def test_private_task_oidc_verifies_audience_and_service_account(monkeypatch):
    settings = SimpleNamespace(
        environment="prod",
        gcp_tasks_oidc_audience="https://worker.example.test",
        gcp_tasks_target_url="https://worker.example.test",
        gcp_tasks_service_account="tasks@example.test",
    )
    monkeypatch.setattr("agent.app.api.tasks.get_settings", lambda: settings)
    monkeypatch.setattr(
        "agent.app.api.tasks.id_token.verify_oauth2_token",
        lambda token, request, audience: {"email": "tasks@example.test", "aud": audience},
    )
    from agent.app.api.tasks import _verify_cloud_tasks_oidc

    _verify_cloud_tasks_oidc(_request())


def test_private_task_oidc_rejects_wrong_service_account(monkeypatch):
    settings = SimpleNamespace(
        environment="prod",
        gcp_tasks_oidc_audience="https://worker.example.test",
        gcp_tasks_target_url="https://worker.example.test",
        gcp_tasks_service_account="tasks@example.test",
    )
    monkeypatch.setattr("agent.app.api.tasks.get_settings", lambda: settings)
    monkeypatch.setattr(
        "agent.app.api.tasks.id_token.verify_oauth2_token",
        lambda token, request, audience: {"email": "other@example.test"},
    )
    from agent.app.api.tasks import _verify_cloud_tasks_oidc

    with pytest.raises(HTTPException) as error:
        _verify_cloud_tasks_oidc(_request())
    assert error.value.status_code == 403


def test_cloud_tasks_uses_deterministic_task_name_for_idempotency(monkeypatch):
    import google.cloud

    created = []

    class FakeClient:
        def task_path(self, project, region, queue, task_id):
            return f"{project}/{region}/{queue}/{task_id}"

        def queue_path(self, project, region, queue):
            return f"{project}/{region}/{queue}"

        def create_task(self, *, parent, task):
            created.append((parent, task))

    factory = lambda **values: SimpleNamespace(**values)
    tasks_v2 = SimpleNamespace(
        CloudTasksClient=FakeClient,
        Task=factory,
        HttpRequest=factory,
        OidcToken=factory,
        HttpMethod=SimpleNamespace(POST="POST"),
    )
    monkeypatch.setattr(google.cloud, "tasks_v2", tasks_v2, raising=False)
    dispatcher = CloudTasksDispatcher(
        project="project",
        queue="queue",
        region="region",
        target_url="https://worker.example.test",
        service_account="tasks@example.test",
        oidc_audience="https://worker.example.test",
    )

    first = dispatcher.dispatch("knowledge.process", lambda: None, "doc_1")
    second = dispatcher.dispatch("knowledge.process", lambda: None, "doc_1")

    assert first == second
    assert len(created) == 2
    assert created[0][1].http_request.url == "https://worker.example.test/internal/tasks/knowledge.process"


def test_vector_search_filters_eligible_chunks_before_distance_ordering(monkeypatch):
    statements = []

    class FakeConnection:
        def execute(self, statement, parameters):
            statements.append((str(statement), parameters))
            return SimpleNamespace(mappings=lambda: [])

    store = ProductStore(
        SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
        Settings(environment="test", rag_enabled=True, rag_embedding_provider="hash", rag_embedding_dimensions=4),
    )

    @contextmanager
    def tenant_transaction(organization_id, **kwargs):
        assert organization_id == "org_a"
        yield FakeConnection()

    monkeypatch.setattr(store, "_tenant_transaction", tenant_transaction)

    assert store.search_knowledge("org_a", "queue backlog") == []
    sql, parameters = statements[0]
    assert sql.index("WITH eligible_chunks") < sql.index("ORDER BY embedding")
    assert "d.organization_id = :organization_id" in sql
    assert parameters["organization_id"] == "org_a"
