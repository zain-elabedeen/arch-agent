from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, inspect, select, text, update

from agent.app.config import Settings
from agent.app.main import app
from agent.app.product import schema
from agent.app.product.knowledge import process_document
from agent.app.product.storage import LocalFilesystemStorage
from agent.app.product.store import DEV_ORG_ID, DEV_OWNER_ID, DEV_STAFF_ID, DEV_VIEWER_ID, ProductStore, utcnow
from agent.app.product.store import get_product_store


@pytest.fixture
def store(tmp_path) -> ProductStore:
    settings = Settings(
        environment="test",
        product_database_url=f"sqlite:///{tmp_path / 'product.db'}",
        collector_registration_endpoint="https://api.example.invalid",
    )
    product_store = ProductStore(create_engine(settings.product_database_url), settings)
    product_store.ensure_schema()
    return product_store


@pytest.fixture
def client(store: ProductStore) -> TestClient:
    app.dependency_overrides[get_product_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _add_org(store: ProductStore, organization_id: str, user_id: str) -> None:
    now = utcnow()
    with store.engine.begin() as conn:
        conn.execute(insert(schema.organizations), {"id": organization_id, "name": organization_id, "slug": organization_id, "created_at": now})
        conn.execute(insert(schema.users), {"id": user_id, "email": f"{user_id}@example.com", "name": user_id, "is_internal": False, "created_at": now})
        conn.execute(insert(schema.organization_memberships), {"organization_id": organization_id, "user_id": user_id, "role": "owner", "created_at": now})


def _ready_doc(store: ProductStore, *, scope: str, organization_id: str | None, title: str, content: str) -> str:
    doc = store.create_document(
        scope=scope,
        organization_id=organization_id,
        actor_user_id=DEV_OWNER_ID,
        title=title,
        filename=f"{title}.md",
        mime_type="text/markdown",
    )
    store.update_document(doc["id"], status="published" if scope == "global" else "ready")
    store.replace_chunks(doc, [SimpleNamespace(chunk_index=0, content=content, content_hash=f"{doc['id']}:0", metadata={})])
    return doc["id"]


def test_all_product_tables_have_updated_at_columns(store: ProductStore) -> None:
    for table in schema.metadata.tables.values():
        assert "updated_at" in table.c
        assert table.c.updated_at.server_default is not None
        assert table.c.updated_at.onupdate is not None


def test_product_updates_refresh_updated_at(store: ProductStore) -> None:
    invitation = store.create_invitation(DEV_ORG_ID, DEV_OWNER_ID, "new@example.com", "viewer")
    with store.engine.begin() as conn:
        conn.execute(
            update(schema.organization_invitations)
            .where(schema.organization_invitations.c.id == invitation["id"])
            .values(updated_at=utcnow().replace(year=2000))
        )

    assert store.revoke_invitation(DEV_ORG_ID, DEV_OWNER_ID, invitation["id"])

    with store.engine.begin() as conn:
        updated_at = conn.execute(
            select(schema.organization_invitations.c.updated_at).where(
                schema.organization_invitations.c.id == invitation["id"]
            )
        ).scalar_one()
    assert updated_at.year > 2000


def test_revoke_invitation_is_idempotent_for_non_pending_local_state(client: TestClient, store: ProductStore) -> None:
    invitation = store.create_invitation(DEV_ORG_ID, DEV_OWNER_ID, "accepted@example.com", "viewer")
    assert store.revoke_invitation(DEV_ORG_ID, DEV_OWNER_ID, invitation["id"])

    response = client.delete(f"/v1/team/invitations/{invitation['id']}")

    assert response.status_code == 204
    assert store.get_invitation(DEV_ORG_ID, invitation["id"])["status"] == "revoked"


def test_schema_initialization_upgrades_legacy_sqlite_database(tmp_path) -> None:
    settings = Settings(environment="test", product_database_url=f"sqlite:///{tmp_path / 'legacy.db'}")
    engine = create_engine(settings.product_database_url)
    schema.metadata.create_all(engine)
    with engine.begin() as conn:
        for table in schema.metadata.tables.values():
            conn.execute(text(f'ALTER TABLE "{table.name}" DROP COLUMN updated_at'))

    ProductStore(engine, settings).ensure_schema()

    inspector = inspect(engine)
    for table in schema.metadata.tables.values():
        updated_at = next(column for column in inspector.get_columns(table.name) if column["name"] == "updated_at")
        assert updated_at["nullable"] is False
        assert updated_at["default"] is not None


def test_scoped_knowledge_never_crosses_organization_boundaries(store: ProductStore) -> None:
    _add_org(store, "org_other", "user_other")
    _ready_doc(store, scope="organization", organization_id=DEV_ORG_ID, title="private-a", content="bulkhead retry timeout")
    _ready_doc(store, scope="organization", organization_id="org_other", title="private-b", content="bulkhead retry timeout")
    _ready_doc(store, scope="global", organization_id=None, title="base", content="bulkhead retry timeout")

    titles = {result.source_title for result in store.search_knowledge(DEV_ORG_ID, "bulkhead retry timeout")}

    assert titles == {"private-a", "base"}
    assert "private-b" not in titles


def test_global_drafts_are_not_retrievable_until_published(store: ProductStore) -> None:
    doc = store.create_document(
        scope="global",
        organization_id=None,
        actor_user_id=DEV_OWNER_ID,
        title="draft",
        filename="draft.md",
        mime_type="text/markdown",
    )
    store.update_document(doc["id"], status="draft")
    store.replace_chunks(doc, [SimpleNamespace(chunk_index=0, content="circuit breaker", content_hash="draft:0", metadata={})])

    assert store.search_knowledge(DEV_ORG_ID, "circuit breaker") == []

    store.update_document(doc["id"], status="published")
    assert [result.source_title for result in store.search_knowledge(DEV_ORG_ID, "circuit breaker")] == ["draft"]


def test_failed_document_scan_is_quarantined_with_safe_error_code(monkeypatch, store: ProductStore, tmp_path) -> None:
    class RejectScanner:
        def scan(self, filename, content):
            raise ValueError("virus signature details must not be persisted")

    document = store.create_document(
        scope="organization",
        organization_id=DEV_ORG_ID,
        actor_user_id=DEV_OWNER_ID,
        title="unsafe",
        filename="unsafe.md",
        mime_type="text/markdown",
    )
    storage = LocalFilesystemStorage(str(tmp_path / "storage"))
    storage.put_bytes(document["object_key"], b"unsafe upload")
    monkeypatch.setattr("agent.app.product.knowledge.get_document_scanner", lambda: RejectScanner())

    with pytest.raises(ValueError):
        process_document(document["id"], store=store, storage=storage)

    failed = store.get_document(document["id"])
    assert failed["status"] == "failed"
    assert not storage.local_path(failed["object_key"]).exists()
    assert storage.read_bytes(f"quarantine/{failed['object_key']}") == b"unsafe upload"
    with store.engine.begin() as conn:
        job = conn.execute(
            select(schema.knowledge_ingestion_jobs).where(schema.knowledge_ingestion_jobs.c.document_id == document["id"])
        ).mappings().one()
    assert job["status"] == "failed"
    assert job["error_code"] == "scan_failed"
    assert "virus" not in str(job)


def test_customer_roles_and_internal_portal_are_enforced(client: TestClient) -> None:
    session = client.get("/v1/session")
    assert session.status_code == 200
    assert session.json()["organizations"][0]["organization_id"] == DEV_ORG_ID
    assert client.get("/v1/organizations").json()[0]["organization_id"] == DEV_ORG_ID
    assert client.post("/v1/team/invitations", headers={"x-archagent-user": "viewer"}, json={"email": "new@example.com"}).status_code == 403
    assert client.post("/v1/team/invitations", json={"email": "new@example.com", "role": "operator"}).status_code == 422
    assert client.get("/internal/v1/accounts").status_code == 403
    assert client.get("/internal/v1/accounts", headers={"x-archagent-user": "staff"}).status_code == 200


def test_internal_admin_payloads_include_full_account_user_and_document_context(client: TestClient, store: ProductStore) -> None:
    headers = {"x-archagent-user": "staff"}
    invitation = store.create_invitation(DEV_ORG_ID, DEV_OWNER_ID, "new@example.com", "viewer")
    analysis_run = store.create_analysis_run(DEV_ORG_ID, DEV_OWNER_ID, "cluster_dev_local", {"source": "test"})
    document = store.create_document(
        scope="global",
        organization_id=None,
        actor_user_id=DEV_STAFF_ID,
        title="Global architecture",
        filename="global.md",
        mime_type="text/markdown",
    )

    account = client.get(f"/internal/v1/accounts/{DEV_ORG_ID}", headers=headers).json()
    assert account["slug"] == "archagent-demo"
    assert account["members"][0]["organization_name"] == "ArchAgent Demo"
    assert account["invitations"][0]["id"] == invitation["id"]
    assert account["clusters_detail"][0]["namespaces"][0]["cluster_id"] == "cluster_dev_local"
    assert account["analysis_runs"][0]["id"] == analysis_run["id"]
    assert account["audit_events"]
    assert "token_hash" not in str(account)

    users = client.get("/internal/v1/users", headers=headers).json()
    owner = next(user for user in users if user["id"] == DEV_OWNER_ID)
    staff = next(user for user in users if user["id"] == DEV_STAFF_ID)
    assert owner["memberships"][0]["organization_id"] == DEV_ORG_ID
    assert staff["is_internal"] is True
    assert staff["memberships"] == []

    documents = client.get("/internal/v1/knowledge/documents", headers=headers).json()
    global_document = next(item for item in documents if item["id"] == document["id"])
    assert global_document["logical_document_id"] == document["id"]
    assert global_document["object_key"].endswith("/global.md")
    assert global_document["updated_at"]


def test_cluster_registration_token_is_scoped_hashed_and_rotated(client: TestClient, store: ProductStore) -> None:
    first = client.post("/v1/clusters/cluster_dev_local/registration-token")
    second = client.post("/v1/clusters/cluster_dev_local/registration-token")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["registration_endpoint"] == "https://api.example.invalid"
    assert first.json()["registration_token"] != second.json()["registration_token"]
    with store.engine.begin() as conn:
        credentials = list(
            conn.execute(
                select(schema.collector_credentials)
                .where(schema.collector_credentials.c.cluster_id == "cluster_dev_local")
                .order_by(schema.collector_credentials.c.created_at)
            ).mappings()
        )
    assert [item["revoked"] for item in credentials] == [True, False]
    assert credentials[1]["token_hash"] == hashlib.sha256(second.json()["registration_token"].encode("utf-8")).hexdigest()
    assert credentials[1]["token_hash"] != second.json()["registration_token"]


def test_collector_registration_is_one_time_and_snapshots_are_tenant_scoped(client: TestClient, store: ProductStore) -> None:
    registration = client.post("/v1/clusters/cluster_dev_local/registration-token").json()["registration_token"]
    exchange = client.post("/collector/v1/register", json={"registration_token": registration})

    assert exchange.status_code == 200
    credential = exchange.json()["credential"]
    assert client.post("/collector/v1/register", json={"registration_token": registration}).status_code == 401

    headers = {"Authorization": f"Bearer {credential}"}
    assert client.post(
        "/collector/v1/heartbeat",
        headers=headers,
        json={
            "version": "0.1.0",
            "last_successful_upload_at": "2026-06-04T23:10:05.000000Z",
            "namespaces": ["default"],
            "modules": {"kubernetes": "healthy"},
        },
    ).status_code == 200
    with store.engine.begin() as conn:
        heartbeat = conn.execute(select(schema.cluster_heartbeats)).mappings().one()
    assert heartbeat["payload"]["last_successful_upload_at"] == "2026-06-04T23:10:05Z"
    snapshot = {
        "signals": {"cpu_utilization": 0.5},
        "services": [{"name": "api", "namespace": "default"}],
        "topology": {"edges": []},
    }
    response = client.post("/collector/v1/snapshots", headers=headers, json={"snapshot": snapshot})
    assert response.status_code == 200
    assert store.load_latest_cluster_snapshot(DEV_ORG_ID, "cluster_dev_local")["snapshot"] == snapshot
    assert store.load_latest_cluster_snapshot("org_other", "cluster_dev_local") is None

    rotated = client.post("/collector/v1/credentials/rotate", headers=headers)
    assert rotated.status_code == 200
    assert client.post("/collector/v1/heartbeat", headers=headers, json={"version": "0.1.0"}).status_code == 401
    assert client.post(
        "/collector/v1/heartbeat",
        headers={"Authorization": f"Bearer {rotated.json()['credential']}"},
        json={"version": "0.1.0"},
    ).status_code == 200


def test_hosted_topology_syncs_namespaces_before_filtering(client: TestClient, store: ProductStore) -> None:
    cluster = store.create_cluster(DEV_ORG_ID, DEV_OWNER_ID, "Hosted Cluster", "production")
    store.store_collector_snapshot(
        {"organization_id": DEV_ORG_ID, "cluster_id": cluster["id"]},
        {
            "services": [
                {
                    "name": "api",
                    "namespace": "default",
                    "cpu": 0.2,
                    "memory": 0.3,
                    "replicas": 2,
                    "restarts": 0,
                }
            ],
            "signals": {"cpu_utilization": 0.2},
            "topology": {"services": ["api"], "edges": [], "service_details": {"api": {"namespace": "default", "replicas": 2}}},
            "data_quality": {"excluded_namespaces": ["kube-system"]},
        },
    )

    response = client.get(f"/v1/topology?cluster_id={cluster['id']}")

    assert response.status_code == 200
    body = response.json()
    assert [node["name"] for node in body["graph"]["nodes"]] == ["api"]
    assert body["data_quality"]["monitored_namespaces"] == ["default"]


def test_expired_collector_registration_token_is_rejected(client: TestClient, store: ProductStore) -> None:
    registration = client.post("/v1/clusters/cluster_dev_local/registration-token").json()["registration_token"]
    with store.engine.begin() as conn:
        conn.execute(
            update(schema.collector_credentials)
            .where(schema.collector_credentials.c.purpose == "registration")
            .values(expires_at=utcnow().replace(year=2000))
        )

    assert client.post("/collector/v1/register", json={"registration_token": registration}).status_code == 401


def test_workos_webhook_lifecycle_events_are_idempotent_and_dependency_ordered(store: ProductStore) -> None:
    membership = {
        "id": "om_test",
        "organization_id": "org_workos",
        "user_id": "user_workos",
        "role": {"slug": "admin"},
        "status": "active",
    }
    assert store.record_workos_event("evt_membership", "organization_membership.created", {"data": membership})
    assert store.apply_workos_event("evt_membership") is False

    assert store.record_workos_event("evt_org", "organization.created", {"data": {"id": "org_workos", "name": "WorkOS Org"}})
    assert store.record_workos_event(
        "evt_user",
        "user.created",
        {"data": {"id": "user_workos", "email": "workos@example.com", "first_name": "WorkOS", "last_name": "User"}},
    )
    assert store.apply_workos_event("evt_org") is True
    assert store.apply_workos_event("evt_user") is True
    assert store.apply_workos_event("evt_membership") is True
    assert store.record_workos_event("evt_membership", "organization_membership.created", {"data": membership}) is False

    with store.engine.begin() as conn:
        row = conn.execute(
            select(schema.organization_memberships)
            .join(schema.organizations, schema.organizations.c.id == schema.organization_memberships.c.organization_id)
            .join(schema.users, schema.users.c.id == schema.organization_memberships.c.user_id)
            .where(schema.organization_memberships.c.workos_membership_id == "om_test")
        ).mappings().one()
    assert row["role"] == "admin"
    assert row["status"] == "active"


def test_workos_default_member_event_does_not_downgrade_local_owner(store: ProductStore) -> None:
    store.upsert_workos_identity(
        workos_user_id="user_owner",
        email="owner@example.com",
        name="Owner",
        workos_organization_id="org_self_serve",
        role="owner",
        is_internal=False,
        organization_name="Owner Workspace",
        workos_membership_id="om_owner",
    )
    assert store.record_workos_event(
        "evt_member_default",
        "organization_membership.updated",
        {
            "data": {
                "id": "om_owner",
                "organization_id": "org_self_serve",
                "user_id": "user_owner",
                "role": {"slug": "member"},
                "status": "active",
            }
        },
    )

    assert store.apply_workos_event("evt_member_default") is True

    with store.engine.begin() as conn:
        membership = conn.execute(
            select(schema.organization_memberships).where(schema.organization_memberships.c.workos_membership_id == "om_owner")
        ).mappings().one()
    assert membership["role"] == "owner"


def test_workos_membership_event_marks_matching_pending_invitation_accepted(store: ProductStore) -> None:
    assert store.record_workos_event(
        "evt_invite_match_org",
        "organization.created",
        {"data": {"id": "org_invite_match", "name": "Invite Match"}},
    )
    assert store.record_workos_event(
        "evt_invite_match_user",
        "user.created",
        {"data": {"id": "user_invite_match", "email": "Invitee@Example.com"}},
    )
    assert store.apply_workos_event("evt_invite_match_org") is True
    assert store.apply_workos_event("evt_invite_match_user") is True
    with store.engine.begin() as conn:
        organization_id = conn.execute(
            select(schema.organizations.c.id).where(schema.organizations.c.workos_organization_id == "org_invite_match")
        ).scalar_one()

    invitation = store.create_invitation(
        organization_id,
        DEV_OWNER_ID,
        "invitee@example.com",
        "viewer",
        workos_invitation_id="inv_invite_match",
    )
    assert store.record_workos_event(
        "evt_invite_match_membership",
        "organization_membership.created",
        {
            "data": {
                "id": "om_invite_match",
                "organization_id": "org_invite_match",
                "user_id": "user_invite_match",
                "role": {"slug": "viewer"},
                "status": "active",
            }
        },
    )

    assert store.apply_workos_event("evt_invite_match_membership") is True

    assert store.get_invitation(organization_id, invitation["id"])["status"] == "accepted"


def test_workos_pending_invitation_event_is_accepted_when_membership_already_exists(store: ProductStore) -> None:
    assert store.record_workos_event(
        "evt_invite_order_org",
        "organization.created",
        {"data": {"id": "org_invite_order", "name": "Invite Order"}},
    )
    assert store.record_workos_event(
        "evt_invite_order_user",
        "user.created",
        {"data": {"id": "user_invite_order", "email": "order@example.com"}},
    )
    assert store.apply_workos_event("evt_invite_order_org") is True
    assert store.apply_workos_event("evt_invite_order_user") is True
    assert store.record_workos_event(
        "evt_invite_order_membership",
        "organization_membership.created",
        {
            "data": {
                "id": "om_invite_order",
                "organization_id": "org_invite_order",
                "user_id": "user_invite_order",
                "role": {"slug": "viewer"},
                "status": "active",
            }
        },
    )
    assert store.apply_workos_event("evt_invite_order_membership") is True
    assert store.record_workos_event(
        "evt_invite_order_invitation",
        "invitation.created",
        {
            "data": {
                "id": "inv_invite_order",
                "organization_id": "org_invite_order",
                "email": "order@example.com",
                "role_slug": "viewer",
                "state": "pending",
            }
        },
    )

    assert store.apply_workos_event("evt_invite_order_invitation") is True

    with store.engine.begin() as conn:
        invitation = conn.execute(
            select(schema.organization_invitations).where(
                schema.organization_invitations.c.workos_invitation_id == "inv_invite_order"
            )
        ).mappings().one()
    assert invitation["status"] == "accepted"


def test_workos_events_cursor_reconciliation_repairs_missed_webhooks(store: ProductStore) -> None:
    from agent.app.product.workos_sync import reconcile_workos_events

    class FakeEvents:
        def list_events(self, *, after=None):
            assert after is None
            return {
                "list": [{"id": "evt_org_reconciled", "event": "organization.created", "data": {"id": "org_reconciled", "name": "Reconciled"}}],
                "list_metadata": {"after": None},
            }

    reconcile_workos_events(store=store, client=FakeEvents())

    with store.engine.begin() as conn:
        organization = conn.execute(
            select(schema.organizations).where(schema.organizations.c.workos_organization_id == "org_reconciled")
        ).mappings().one()
    assert organization["name"] == "Reconciled"


def test_workos_sync_ignores_stale_membership_events(store: ProductStore) -> None:
    assert store.record_workos_event(
        "evt_org_timestamped",
        "organization.created",
        {"data": {"id": "org_timestamped", "name": "Timestamped", "updated_at": "2026-06-02T12:00:00Z"}},
    )
    assert store.record_workos_event(
        "evt_user_timestamped",
        "user.created",
        {"data": {"id": "user_timestamped", "email": "timestamped@example.com", "updated_at": "2026-06-02T12:00:00Z"}},
    )
    store.apply_workos_event("evt_org_timestamped")
    store.apply_workos_event("evt_user_timestamped")
    assert store.record_workos_event(
        "evt_membership_new",
        "organization_membership.updated",
        {
            "data": {
                "id": "om_timestamped",
                "organization_id": "org_timestamped",
                "user_id": "user_timestamped",
                "role": {"slug": "admin"},
                "updated_at": "2026-06-02T12:00:00Z",
            }
        },
    )
    assert store.record_workos_event(
        "evt_membership_stale",
        "organization_membership.updated",
        {
            "data": {
                "id": "om_timestamped",
                "organization_id": "org_timestamped",
                "user_id": "user_timestamped",
                "role": {"slug": "viewer"},
                "updated_at": "2026-06-01T12:00:00Z",
            }
        },
    )

    store.apply_workos_event("evt_membership_new")
    store.apply_workos_event("evt_membership_stale")

    with store.engine.begin() as conn:
        membership = conn.execute(
            select(schema.organization_memberships).where(schema.organization_memberships.c.workos_membership_id == "om_timestamped")
        ).mappings().one()
    assert membership["role"] == "admin"


def test_workos_sync_upserts_externally_created_invitation(store: ProductStore) -> None:
    assert store.record_workos_event(
        "evt_invitation_org",
        "organization.created",
        {"data": {"id": "org_invitation", "name": "Invitation Org"}},
    )
    store.apply_workos_event("evt_invitation_org")
    assert store.record_workos_event(
        "evt_invitation_created",
        "invitation.created",
        {
            "data": {
                "id": "inv_workos",
                "organization_id": "org_invitation",
                "email": "External@Example.com",
                "role_slug": "admin",
                "state": "pending",
            }
        },
    )
    assert store.record_workos_event(
        "evt_invitation_accepted",
        "invitation.accepted",
        {"data": {"id": "inv_workos", "state": "accepted"}},
    )

    assert store.apply_workos_event("evt_invitation_created") is True
    assert store.apply_workos_event("evt_invitation_accepted") is True

    with store.engine.begin() as conn:
        invitation = conn.execute(
            select(schema.organization_invitations).where(
                schema.organization_invitations.c.workos_invitation_id == "inv_workos"
            )
        ).mappings().one()
    assert invitation["email"] == "external@example.com"
    assert invitation["role"] == "admin"
    assert invitation["status"] == "accepted"
    assert invitation["invited_by_user_id"] is None


def test_local_namespace_discovery_repairs_stale_allow_list(store: ProductStore) -> None:
    store.replace_namespaces(
        DEV_ORG_ID,
        DEV_OWNER_ID,
        "cluster_dev_local",
        [
            {"namespace": "default", "monitored": False, "is_system": False},
            {"namespace": "kube-system", "monitored": True, "is_system": True},
        ],
    )

    namespaces = store.sync_discovered_namespaces(
        DEV_ORG_ID,
        "cluster_dev_local",
        {"june-sim"},
        {"kube-system", "kube-public"},
    )
    by_name = {item["namespace"]: item for item in namespaces}

    assert by_name["june-sim"]["monitored"] is True
    assert by_name["kube-system"]["monitored"] is False
    assert by_name["kube-public"]["monitored"] is False


def test_analysis_run_history_is_listed_for_viewers(client: TestClient, store: ProductStore) -> None:
    run = store.create_analysis_run(DEV_ORG_ID, DEV_OWNER_ID, "cluster_dev_local", {"source": "test"})
    store.complete_analysis_run(
        DEV_ORG_ID,
        run["id"],
        status="completed",
        result_payload={"smell_count": 2, "recommendation_count": 3},
    )

    response = client.get(
        "/v1/analysis-runs?cluster_id=cluster_dev_local",
        headers={"x-archagent-user": "viewer"},
    )

    assert response.status_code == 200
    assert response.json()[0]["id"] == run["id"]
    assert response.json()[0]["status"] == "completed"


def test_recommendations_persist_completed_analysis_history(monkeypatch, client: TestClient) -> None:
    snapshot_id = uuid4()

    def fake_fetch(settings, run_id):
        return {"cpu_utilization": 0.5}, {"services": ["api"], "edges": []}, {}, snapshot_id

    class FakeGraph:
        def invoke(self, state):
            return {
                **state,
                "smells": [{"type": "single_instance_risk"}],
                "recommendations": [
                    {
                        "pattern": "horizontal_scaling",
                        "solution": "Scale api horizontally.",
                        "impact": "high",
                        "effort": "medium",
                    }
                ],
                "critiques": [],
                "final_plan": [
                    {
                        "title": "Scale api",
                        "description": "Add replicas for api.",
                        "impact": "high",
                        "effort": "medium",
                    }
                ],
                "explanation_source": "deterministic",
            }

    monkeypatch.setattr("agent.app.api.recommendations.fetch_snapshot_raw", fake_fetch)
    monkeypatch.setattr("agent.app.api.recommendations.build_graph", lambda settings: FakeGraph())

    response = client.post("/v1/recommendations?cluster_id=cluster_dev_local", json={})
    history = client.get("/v1/analysis-runs?cluster_id=cluster_dev_local")

    assert response.status_code == 200
    assert history.status_code == 200
    assert history.json()[0]["status"] == "completed"
    assert history.json()[0]["result_payload"]["snapshot_run_id"] == str(snapshot_id)
    assert history.json()[0]["result_payload"]["recommendation_count"] == 1


def test_production_rejects_local_product_adapters() -> None:
    with pytest.raises(ValueError, match="Production cannot use local product adapters"):
        Settings(environment="prod")


def test_schema_initialization_is_serialized_across_store_instances(tmp_path, monkeypatch) -> None:
    settings = Settings(environment="test", product_database_url=f"sqlite:///{tmp_path / 'startup.db'}")
    stores = [ProductStore(create_engine(settings.product_database_url), settings) for _ in range(8)]
    barrier = Barrier(len(stores))
    measurement_lock = Lock()
    active = 0
    max_active = 0
    original_create_all = schema.metadata.create_all

    def measured_create_all(engine) -> None:
        nonlocal active, max_active
        with measurement_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.01)
        try:
            original_create_all(engine)
        finally:
            with measurement_lock:
                active -= 1

    monkeypatch.setattr(schema.metadata, "create_all", measured_create_all)

    def initialize(store: ProductStore) -> None:
        barrier.wait()
        store.ensure_schema()

    with ThreadPoolExecutor(max_workers=len(stores)) as executor:
        list(executor.map(initialize, stores))

    assert max_active == 1
