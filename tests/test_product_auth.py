from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from agent.app.config import Settings, get_settings
from agent.app.main import _service_path_allowed, app, create_app
from agent.app.product import schema
from agent.app.product.store import ProductStore, get_product_store
from agent.app.product.workos_client import WorkOSClientAdapter, get_workos_client


def _auth_result(*, organization_id="org_workos", sealed_session="sealed-session", role="owner"):
    return {
        "authenticated": True,
        "sealed_session": sealed_session,
        "user": {"id": "user_workos", "email": "owner@example.com", "first_name": "WorkOS", "last_name": "Owner"},
        "organization_id": organization_id,
        "role": role,
        "permissions": ["analysis:run"],
    }


class FakeSession:
    def __init__(self, authenticate_result=None, refresh_result=None):
        self.authenticate_result = authenticate_result or _auth_result()
        self.refresh_result = refresh_result or _auth_result()
        self.refresh_organization_id = None
        self.logout_return_to = None

    def authenticate(self):
        return self.authenticate_result

    def refresh(self, organization_id=None):
        self.refresh_organization_id = organization_id
        return self.refresh_result

    def get_logout_url(self, return_to=None):
        self.logout_return_to = return_to
        return "https://auth.example.test/logout"


def _set_cookie_headers(response) -> list[str]:
    return response.headers.get_list("set-cookie")


def _prod_settings(**values):
    defaults = {
        "environment": "prod",
        "auth_mode": "workos",
        "product_database_url": "postgresql+psycopg://archagent:test@db/archagent",
        "postgres_dsn": "postgresql+psycopg://archagent:test@db/archagent",
        "storage_backend": "gcs",
        "task_dispatcher": "cloud_tasks",
        "document_scan_mode": "clamav",
        "workos_api_key": "api-key",
        "workos_client_id": "client-id",
        "workos_cookie_password": "cookie-password",
        "workos_webhook_secret": "webhook-secret",
        "workos_webhook_path_token": "path-token",
        "workos_redirect_uri": "http://localhost:8000/auth/callback",
        "csrf_secret": "csrf-secret",
        "gcp_storage_bucket": "knowledge",
        "gcp_quarantine_bucket": "quarantine",
        "gcp_project_id": "test-project",
        "gcp_tasks_queue": "worker",
        "gcp_tasks_target_url": "https://worker.example.test",
        "gcp_tasks_service_account": "tasks@test-project.iam.gserviceaccount.com",
        "gcp_tasks_oidc_audience": "https://worker.example.test",
        "scanner_service_url": "https://scanner.example.test",
        "scanner_service_token": "scanner-token",
        "allowed_origin_regex": "",
    }
    defaults.update(values)
    return Settings(**defaults)


def test_workos_adapter_uses_v8_session_and_membership_apis(monkeypatch):
    calls = {}

    class FakeUserManagement:
        def get_authorization_url(self, **kwargs):
            calls["authorize"] = kwargs
            return "https://auth.example.test/login"

        def authenticate_with_code(self, **kwargs):
            calls["authenticate"] = kwargs
            return SimpleNamespace(
                to_dict=lambda: {
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "user": {"id": "user_workos", "email": "owner@example.com"},
                }
            )

        def load_sealed_session(self, **kwargs):
            calls["load"] = kwargs
            return "loaded-session"

    class FakeOrganizationMembership:
        def create_organization_membership(self, **kwargs):
            calls["create_membership"] = kwargs
            return SimpleNamespace(id="om_self_serve")

        def update_organization_membership(self, *args, **kwargs):
            calls["update_membership"] = (args, kwargs)

        def deactivate_organization_membership(self, *args, **kwargs):
            calls["deactivate_membership"] = (args, kwargs)

    class FakeOrganizations:
        def get_organization_by_external_id(self, external_id):
            calls["get_organization_by_external_id"] = external_id
            error = Exception("not found")
            error.status_code = 404
            raise error

        def create_organization(self, **kwargs):
            calls["create_organization"] = kwargs
            return SimpleNamespace(id="org_self_serve", name=kwargs["name"])

    class FakeWebhooks:
        def verify_event(self, **kwargs):
            calls["verify_event"] = kwargs
            return {"id": "event_test", "event": "user.created"}

    class FakeSDKClient:
        def __init__(self, **kwargs):
            calls["client"] = kwargs
            self.user_management = FakeUserManagement()
            self.organizations = FakeOrganizations()
            self.organization_membership = FakeOrganizationMembership()
            self.webhooks = FakeWebhooks()

    def seal_session_from_auth_response(**kwargs):
        calls["seal"] = kwargs
        return "sealed-session"

    workos_module = ModuleType("workos")
    workos_module.WorkOSClient = FakeSDKClient
    session_module = ModuleType("workos.session")
    session_module.seal_session_from_auth_response = seal_session_from_auth_response
    monkeypatch.setitem(sys.modules, "workos", workos_module)
    monkeypatch.setitem(sys.modules, "workos.session", session_module)

    adapter = WorkOSClientAdapter(_prod_settings())

    assert adapter.authorization_url(state="http://localhost:5173/overview") == "https://auth.example.test/login"
    assert calls["authorize"] == {
        "provider": "authkit",
        "redirect_uri": "http://localhost:8000/auth/callback",
        "prompt": "login",
        "state": "http://localhost:5173/overview",
    }

    response = adapter.authenticate_with_code("valid-code")
    assert calls["authenticate"] == {"code": "valid-code"}
    assert calls["seal"]["cookie_password"] == "cookie-password"
    assert response["sealed_session"] == "sealed-session"
    assert adapter.load_session("sealed-session") == "loaded-session"
    assert calls["load"] == {"session_data": "sealed-session", "cookie_password": "cookie-password"}

    provisioned = adapter.provision_self_serve_organization(
        workos_user_id="user_workos",
        email="owner@example.com",
        name="WorkOS Owner",
    )
    assert provisioned["organization_id"] == "org_self_serve"
    assert provisioned["membership_id"] == "om_self_serve"
    assert provisioned["role"] == "owner"
    assert calls["create_membership"] == {"user_id": "user_workos", "organization_id": "org_self_serve"}

    adapter.update_membership("membership-id", "admin")
    adapter.deactivate_membership("membership-id")
    assert calls["update_membership"] == (("membership-id",), {"role": "admin"})
    assert calls["deactivate_membership"] == (("membership-id",), {})

    assert adapter.construct_webhook_event(b"{}", "t=1,v1=abc") == {"id": "event_test", "event": "user.created"}
    assert calls["verify_event"] == {
        "event_body": b"{}",
        "event_signature": "t=1, v1=abc",
        "secret": "webhook-secret",
    }


def test_workos_adapter_treats_non_pending_invitation_revoke_as_stale_local_state(monkeypatch):
    calls = {}

    class FakeUserManagement:
        def revoke_invitation(self, invitation_id):
            calls["revoke_invitation"] = invitation_id
            error = Exception("Invite is not pending.")
            error.code = "invite_not_pending"
            raise error

    class FakeSDKClient:
        def __init__(self, **kwargs):
            self.user_management = FakeUserManagement()

    workos_module = ModuleType("workos")
    workos_module.WorkOSClient = FakeSDKClient
    monkeypatch.setitem(sys.modules, "workos", workos_module)

    adapter = WorkOSClientAdapter(_prod_settings())

    assert adapter.revoke_invitation("invitation_not_pending") is False
    assert calls["revoke_invitation"] == "invitation_not_pending"


class FakeWorkOS:
    def __init__(self, session=None, callback_result=None, provision_result=None):
        self.session = session or FakeSession()
        self.callback_result = callback_result or _auth_result()
        self.provision_result = provision_result or {
            "organization_id": "org_self_serve",
            "organization_name": "WorkOS Owner's Workspace",
            "membership_id": "om_self_serve",
            "role": "owner",
        }
        self.provision_calls = []
        self.authorization_state = None

    def authorization_url(self, state=None):
        self.authorization_state = state
        return "https://auth.example.test/login"

    def authenticate_with_code(self, code):
        assert code == "valid-code"
        return self.callback_result

    def load_session(self, sealed_session):
        return self.session

    def provision_self_serve_organization(self, **kwargs):
        self.provision_calls.append(kwargs)
        return self.provision_result


@pytest.fixture
def workos_client(monkeypatch, tmp_path):
    monkeypatch.setenv("ARCHAGENT_AUTH_MODE", "workos")
    monkeypatch.setenv("ARCHAGENT_WORKOS_API_KEY", "test-api-key")
    monkeypatch.setenv("ARCHAGENT_WORKOS_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("ARCHAGENT_WORKOS_COOKIE_PASSWORD", "test-cookie-password")
    monkeypatch.setenv("ARCHAGENT_CSRF_SECRET", "test-csrf-secret")
    monkeypatch.setenv("ARCHAGENT_WORKOS_POST_LOGIN_REDIRECT", "http://localhost:8000/v1/session")
    monkeypatch.setenv("ARCHAGENT_WORKOS_SIGN_OUT_REDIRECT", "http://localhost:8000/auth/signed-out")
    monkeypatch.setenv(
        "ARCHAGENT_ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://localhost:8080,http://localhost:8081,"
        "http://127.0.0.1:8080,http://127.0.0.1:8081",
    )
    monkeypatch.setenv("ARCHAGENT_ALLOWED_ORIGIN_REGEX", "")
    monkeypatch.setenv("ARCHAGENT_COOKIE_SAMESITE", "lax")
    get_settings.cache_clear()
    get_workos_client.cache_clear()
    store = ProductStore(create_engine(f"sqlite:///{tmp_path / 'auth.db'}"), Settings(environment="test"))
    store.ensure_schema()
    app.dependency_overrides[get_product_store] = lambda: store
    with TestClient(app) as client:
        yield client, store
    app.dependency_overrides.clear()
    get_settings.cache_clear()
    get_workos_client.cache_clear()


def test_dashboard_origin_is_allowed_for_session_cors_preflight(monkeypatch):
    monkeypatch.delenv("ARCHAGENT_ALLOWED_ORIGINS", raising=False)
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        response = client.options(
            "/v1/session",
            headers={
                "Origin": "https://app.archagent.de",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.archagent.de"


def test_workos_callback_sets_secure_http_only_sealed_cookie(monkeypatch, workos_client):
    client, _ = workos_client
    fake = FakeWorkOS()
    monkeypatch.setattr("agent.app.api.auth.get_workos_client", lambda: fake)

    response = client.get("/auth/callback?code=valid-code", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "http://localhost:8000/v1/session"
    cookie = response.headers["set-cookie"]
    assert "__Host-archagent-session=sealed-session" in cookie
    assert "Max-Age=2592000" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie


def test_workos_login_passes_allowed_return_to_as_oauth_state(monkeypatch, workos_client):
    client, _ = workos_client
    fake = FakeWorkOS()
    monkeypatch.setattr("agent.app.api.auth.get_workos_client", lambda: fake)

    response = client.get(
        "/auth/login?return_to=http%3A%2F%2Flocalhost%3A5173%2Foverview",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "https://auth.example.test/login"
    assert fake.authorization_state == "http://localhost:5173/overview"


def test_workos_login_allows_vite_preview_return_to(monkeypatch, workos_client):
    client, _ = workos_client
    fake = FakeWorkOS()
    monkeypatch.setattr("agent.app.api.auth.get_workos_client", lambda: fake)

    response = client.get(
        "/auth/login?return_to=http%3A%2F%2F127.0.0.1%3A8081%2Foverview",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert fake.authorization_state == "http://127.0.0.1:8081/overview"


def test_workos_login_allows_return_to_by_origin_regex(monkeypatch, workos_client):
    client, _ = workos_client
    monkeypatch.setenv("ARCHAGENT_ALLOWED_ORIGIN_REGEX", r"https://(.*\.)?lovableproject\.com")
    get_settings.cache_clear()
    fake = FakeWorkOS()
    monkeypatch.setattr("agent.app.api.auth.get_workos_client", lambda: fake)

    response = client.get(
        "/auth/login?return_to=https%3A%2F%2Fpreview-123.lovableproject.com%2Foverview",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert fake.authorization_state == "https://preview-123.lovableproject.com/overview"


def test_workos_callback_redirects_to_allowed_state(monkeypatch, workos_client):
    client, _ = workos_client
    fake = FakeWorkOS()
    monkeypatch.setattr("agent.app.api.auth.get_workos_client", lambda: fake)

    response = client.get(
        "/auth/callback?code=valid-code&state=http%3A%2F%2Flocalhost%3A5173%2Foverview",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "http://localhost:5173/overview"


def test_workos_callback_rejects_untrusted_state_redirect(monkeypatch, workos_client):
    client, _ = workos_client
    fake = FakeWorkOS()
    monkeypatch.setattr("agent.app.api.auth.get_workos_client", lambda: fake)

    response = client.get(
        "/auth/callback?code=valid-code&state=https%3A%2F%2Fevil.example.test%2Foverview",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "http://localhost:8000/v1/session"


def test_workos_callback_auto_provisions_workspace_for_direct_signup(monkeypatch, workos_client):
    client, store = workos_client
    fake = FakeWorkOS(
        session=FakeSession(authenticate_result=_auth_result(organization_id=None)),
        callback_result=_auth_result(organization_id=None),
    )
    monkeypatch.setattr("agent.app.api.auth.get_workos_client", lambda: fake)
    monkeypatch.setattr("agent.app.product.auth.get_workos_client", lambda: fake)

    response = client.get("/auth/callback?code=valid-code", follow_redirects=False)

    assert response.status_code == 302
    assert fake.provision_calls == [
        {"workos_user_id": "user_workos", "email": "owner@example.com", "name": "WorkOS Owner"}
    ]
    with store.engine.begin() as conn:
        user = conn.execute(select(schema.users).where(schema.users.c.workos_user_id == "user_workos")).mappings().one()
        membership = conn.execute(
            select(schema.organization_memberships).where(schema.organization_memberships.c.user_id == user["id"])
        ).mappings().one()
        organization = conn.execute(
            select(schema.organizations).where(schema.organizations.c.id == membership["organization_id"])
        ).mappings().one()
    assert organization["workos_organization_id"] == "org_self_serve"
    assert organization["name"] == "WorkOS Owner's Workspace"
    assert membership["role"] == "owner"
    assert membership["workos_membership_id"] == "om_self_serve"

    session_response = client.get("/v1/session", cookies={"__Host-archagent-session": "sealed-session"})

    assert session_response.status_code == 200
    assert session_response.json()["organization_id"] == organization["id"]
    assert session_response.json()["role"] == "owner"


def test_workos_callback_maps_nested_role_slug(monkeypatch, workos_client):
    client, store = workos_client
    fake = FakeWorkOS(callback_result=_auth_result(role={"slug": "owner"}))
    monkeypatch.setattr("agent.app.api.auth.get_workos_client", lambda: fake)

    response = client.get("/auth/callback?code=valid-code", follow_redirects=False)

    assert response.status_code == 302
    with store.engine.begin() as conn:
        user = conn.execute(select(schema.users).where(schema.users.c.workos_user_id == "user_workos")).mappings().one()
        membership = conn.execute(
            select(schema.organization_memberships).where(schema.organization_memberships.c.user_id == user["id"])
        ).mappings().one()
    assert membership["role"] == "owner"


def test_workos_callback_without_role_does_not_downgrade_local_owner(monkeypatch, workos_client):
    client, store = workos_client
    local = store.upsert_workos_identity(
        workos_user_id="user_workos",
        email="owner@example.com",
        name="WorkOS Owner",
        workos_organization_id="org_workos",
        role="owner",
        is_internal=False,
        organization_name="Owner Workspace",
        workos_membership_id="om_self_serve",
    )
    callback_result = _auth_result()
    callback_result.pop("role")
    fake = FakeWorkOS(callback_result=callback_result)
    monkeypatch.setattr("agent.app.api.auth.get_workos_client", lambda: fake)

    response = client.get("/auth/callback?code=valid-code", follow_redirects=False)

    assert response.status_code == 302
    with store.engine.begin() as conn:
        membership = conn.execute(
            select(schema.organization_memberships).where(schema.organization_memberships.c.user_id == local["user_id"])
        ).mappings().one()
    assert membership["role"] == "owner"


def test_workos_callback_maps_roles_collection(monkeypatch, workos_client):
    client, store = workos_client
    callback_result = _auth_result(role=None)
    callback_result["roles"] = ["viewer", "owner"]
    fake = FakeWorkOS(callback_result=callback_result)
    monkeypatch.setattr("agent.app.api.auth.get_workos_client", lambda: fake)

    response = client.get("/auth/callback?code=valid-code", follow_redirects=False)

    assert response.status_code == 302
    with store.engine.begin() as conn:
        user = conn.execute(select(schema.users).where(schema.users.c.workos_user_id == "user_workos")).mappings().one()
        membership = conn.execute(
            select(schema.organization_memberships).where(schema.organization_memberships.c.user_id == user["id"])
        ).mappings().one()
    assert membership["role"] == "owner"


def test_workos_identity_relinks_existing_email_when_user_is_recreated(workos_client):
    _, store = workos_client
    first = store.upsert_workos_identity(
        workos_user_id="user_old",
        email="Owner@Example.com",
        name="Old Owner",
        workos_organization_id=None,
        role="viewer",
        is_internal=False,
    )

    second = store.upsert_workos_identity(
        workos_user_id="user_new",
        email=" owner@example.com ",
        name="New Owner",
        workos_organization_id="org_new",
        role="owner",
        is_internal=False,
        organization_name="New Workspace",
        workos_membership_id="om_new",
    )

    assert second["user_id"] == first["user_id"]
    assert store.get_workos_user_id(first["user_id"]) == "user_new"
    assert second["email"] == "owner@example.com"


def test_workos_default_member_role_does_not_downgrade_local_owner(workos_client):
    _, store = workos_client
    first = store.upsert_workos_identity(
        workos_user_id="user_workos",
        email="owner@example.com",
        name="WorkOS Owner",
        workos_organization_id="org_self_serve",
        role="owner",
        is_internal=False,
        organization_name="Owner Workspace",
        workos_membership_id="om_self_serve",
    )

    second = store.upsert_workos_identity(
        workos_user_id="user_workos",
        email="owner@example.com",
        name="WorkOS Owner",
        workos_organization_id="org_self_serve",
        role="member",
        is_internal=False,
        organization_name="Owner Workspace",
        workos_membership_id="om_self_serve",
    )

    assert first["role"] == "owner"
    assert second["role"] == "owner"


def test_workos_session_auto_provisions_workspace_for_existing_pending_signup(monkeypatch, workos_client):
    client, _ = workos_client
    fake = FakeWorkOS(session=FakeSession(authenticate_result=_auth_result(organization_id=None)))
    monkeypatch.setattr("agent.app.product.auth.get_workos_client", lambda: fake)

    response = client.get("/v1/session", cookies={"__Host-archagent-session": "pending-session"})

    assert response.status_code == 200
    assert fake.provision_calls == [
        {"workos_user_id": "user_workos", "email": "owner@example.com", "name": "WorkOS Owner"}
    ]
    assert response.json()["organization_id"]
    assert response.json()["role"] == "owner"


def test_expired_workos_session_refreshes_and_rotates_cookie(monkeypatch, workos_client):
    client, store = workos_client
    store.upsert_workos_identity(
        workos_user_id="user_workos",
        email="owner@example.com",
        name="WorkOS Owner",
        workos_organization_id="org_workos",
        role="owner",
        is_internal=False,
    )
    fake = FakeWorkOS(
        session=FakeSession(
            authenticate_result={"authenticated": False, "reason": "access_token_expired"},
            refresh_result=_auth_result(sealed_session="rotated-session"),
        )
    )
    monkeypatch.setattr("agent.app.product.auth.get_workos_client", lambda: fake)

    response = client.get("/v1/session", cookies={"__Host-archagent-session": "expired-session"})

    assert response.status_code == 200
    assert "rotated-session" in response.headers["set-cookie"]
    assert "Max-Age=2592000" in response.headers["set-cookie"]


def test_missing_workos_session_cookie_returns_401(monkeypatch, workos_client):
    client, _ = workos_client
    monkeypatch.setattr(
        "agent.app.product.auth.get_workos_client",
        lambda: pytest.fail("WorkOS SDK must not be called without a session cookie."),
    )

    response = client.get("/v1/session")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required."}


def test_topology_accepts_workos_session_cookie(monkeypatch, workos_client):
    client, store = workos_client
    local = store.upsert_workos_identity(
        workos_user_id="user_workos",
        email="owner@example.com",
        name="WorkOS Owner",
        workos_organization_id="org_workos",
        role="owner",
        is_internal=False,
        organization_name="Owner Workspace",
        workos_membership_id="om_self_serve",
    )
    store.create_cluster(local["organization_id"], local["user_id"], "Production", "production")
    fake = FakeWorkOS(session=FakeSession(authenticate_result=_auth_result(organization_id="org_workos")))
    monkeypatch.setattr("agent.app.product.auth.get_workos_client", lambda: fake)
    snap = uuid4()

    def fake_snapshot(store, organization_id, cluster_id, run_id=None):
        return {
            "id": str(snap),
            "snapshot": {
                "topology": {
                    "graph": {
                        "nodes": [{"id": "k8s:workload:api", "name": "api", "kind": "workload"}],
                        "edges": [],
                        "meta": {"run_id": str(snap)},
                    }
                },
                "data_quality": {},
            },
        }

    monkeypatch.setattr("agent.app.api.topology.get_hosted_snapshot", fake_snapshot)

    response = client.get("/v1/topology", cookies={"__Host-archagent-session": "sealed-session"})

    assert response.status_code == 200
    assert response.json()["snapshot_run_id"] == str(snap)


def test_logout_requires_csrf_and_clears_auth_cookies(monkeypatch, workos_client):
    client, _ = workos_client
    fake = FakeWorkOS()
    monkeypatch.setattr("agent.app.api.auth.get_workos_client", lambda: fake)
    token = client.get("/auth/csrf").json()["csrf_token"]

    assert client.post("/auth/logout").status_code == 403
    response = client.post(
        "/auth/logout",
        headers={"x-archagent-csrf": token},
        cookies={"__Host-archagent-session": "sealed", "__Host-archagent-csrf": token},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "https://auth.example.test/logout"
    assert fake.session.logout_return_to == "http://localhost:8000/auth/signed-out"
    set_cookies = _set_cookie_headers(response)
    session_delete = next(item for item in set_cookies if item.startswith("__Host-archagent-session="))
    csrf_delete = next(item for item in set_cookies if item.startswith("__Host-archagent-csrf="))
    assert "__Host-archagent-session=\"\"" in session_delete
    assert "Max-Age=0" in session_delete
    assert "Secure" in session_delete
    assert "HttpOnly" in session_delete
    assert "SameSite=lax" in session_delete
    assert "__Host-archagent-csrf=\"\"" in csrf_delete
    assert "Max-Age=0" in csrf_delete
    assert "Secure" in csrf_delete
    assert "HttpOnly" not in csrf_delete
    assert "SameSite=lax" in csrf_delete


def test_logout_json_returns_workos_logout_url_and_clears_auth_cookies(monkeypatch, workos_client):
    client, _ = workos_client
    fake = FakeWorkOS()
    monkeypatch.setattr("agent.app.api.auth.get_workos_client", lambda: fake)
    token = client.get("/auth/csrf").json()["csrf_token"]

    response = client.post(
        "/auth/logout?format=json&return_to=http%3A%2F%2Flocalhost%3A5173%2Flogin",
        headers={"x-archagent-csrf": token},
        cookies={"__Host-archagent-session": "sealed", "__Host-archagent-csrf": token},
    )

    assert response.status_code == 200
    assert response.json() == {
        "logout_url": "https://auth.example.test/logout",
        "redirect_to": "http://localhost:5173/login",
    }
    assert fake.session.logout_return_to == "http://localhost:5173/login"
    set_cookies = _set_cookie_headers(response)
    assert any(item.startswith("__Host-archagent-session=") and "Max-Age=0" in item for item in set_cookies)
    assert any(item.startswith("__Host-archagent-csrf=") and "Max-Age=0" in item for item in set_cookies)


def test_csrf_cookie_uses_configured_ttl(workos_client):
    client, _ = workos_client

    response = client.get("/auth/csrf")

    assert response.status_code == 200
    assert "Max-Age=2592000" in response.headers["set-cookie"]


def test_logout_clears_cookies_when_workos_session_is_invalid(monkeypatch, workos_client):
    client, _ = workos_client

    class InvalidLogoutSession(FakeSession):
        def get_logout_url(self, return_to=None):
            raise ValueError("Failed to extract session ID for logout URL: INVALID_JWT")

    fake = FakeWorkOS(session=InvalidLogoutSession())
    monkeypatch.setattr("agent.app.api.auth.get_workos_client", lambda: fake)
    token = client.get("/auth/csrf").json()["csrf_token"]

    response = client.post(
        "/auth/logout",
        headers={"x-archagent-csrf": token},
        cookies={"__Host-archagent-session": "invalid", "__Host-archagent-csrf": token},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "http://localhost:8000/auth/signed-out"
    set_cookies = _set_cookie_headers(response)
    assert any(item.startswith("__Host-archagent-session=") and "Secure" in item for item in set_cookies)
    assert any(item.startswith("__Host-archagent-csrf=") and "Secure" in item for item in set_cookies)


def test_logout_uses_allowed_request_origin_when_configured_redirect_is_stale(monkeypatch, workos_client):
    client, _ = workos_client
    monkeypatch.setenv("ARCHAGENT_WORKOS_SIGN_OUT_REDIRECT", "https://stale.lovableproject.com/overview")
    get_settings.cache_clear()
    fake = FakeWorkOS()
    monkeypatch.setattr("agent.app.api.auth.get_workos_client", lambda: fake)
    token = client.get("/auth/csrf").json()["csrf_token"]

    response = client.post(
        "/auth/logout",
        headers={"x-archagent-csrf": token, "origin": "http://localhost:5173"},
        cookies={"__Host-archagent-session": "sealed", "__Host-archagent-csrf": token},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "https://auth.example.test/logout"
    assert fake.session.logout_return_to == "http://localhost:5173/overview"


def test_organization_switch_uses_workos_refresh_and_rotates_cookie(monkeypatch, workos_client):
    client, _ = workos_client
    session = FakeSession(
        authenticate_result=_auth_result(organization_id="org_a"),
        refresh_result=_auth_result(organization_id="org_b", sealed_session="org-b-session", role="admin"),
    )
    fake = FakeWorkOS(session=session)
    monkeypatch.setattr("agent.app.product.auth.get_workos_client", lambda: fake)
    monkeypatch.setattr("agent.app.api.auth.get_workos_client", lambda: fake)
    token = client.get("/auth/csrf").json()["csrf_token"]

    response = client.post(
        "/v1/session/organization",
        json={"organization_id": "org_b"},
        headers={"x-archagent-csrf": token},
        cookies={"__Host-archagent-session": "org-a-session", "__Host-archagent-csrf": token},
    )

    assert response.status_code == 200
    assert session.refresh_organization_id == "org_b"
    assert response.json()["workos_organization_id"] == "org_b"
    assert "org-b-session" in response.headers["set-cookie"]


def test_production_rejects_development_identity_headers(monkeypatch):
    settings = _prod_settings()
    monkeypatch.setattr("agent.app.main.get_settings", lambda: settings)

    with TestClient(create_app()) as client:
        response = client.get("/healthz", headers={"x-archagent-user": "owner"})

    assert response.status_code == 400


def test_production_ingest_service_hides_staff_routes(monkeypatch):
    monkeypatch.setattr("agent.app.main.get_settings", lambda: _prod_settings(service_role="ingest"))

    with TestClient(create_app()) as client:
        response = client.get("/internal/v1/accounts")

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("service_role", "path", "allowed"),
    [
        ("api", "/v1/session", True),
        ("api", "/collector/v1/heartbeat", False),
        ("api", "/internal/tasks/analysis.process", False),
        ("ops", "/internal/v1/accounts", True),
        ("ops", "/v1/session", False),
        ("ingest", "/collector/v1/heartbeat", True),
        ("ingest", "/internal/v1/accounts", False),
        ("worker", "/internal/tasks/analysis.process", True),
        ("worker", "/v1/session", False),
        ("worker", "/healthz", True),
    ],
)
def test_production_service_route_surface(service_role, path, allowed):
    assert _service_path_allowed(service_role, path) is allowed


def test_production_ingest_profile_does_not_require_unowned_secrets():
    settings = Settings(
        environment="prod",
        service_role="ingest",
        product_database_url="postgresql+psycopg://archagent:test@db/archagent",
        task_dispatcher="cloud_tasks",
        gcp_project_id="test-project",
        gcp_tasks_queue="worker",
        gcp_tasks_target_url="https://worker.example.test",
        gcp_tasks_service_account="tasks@test-project.iam.gserviceaccount.com",
        gcp_tasks_oidc_audience="https://worker.example.test",
        workos_api_key=None,
        scanner_service_token=None,
        allowed_origin_regex="",
    )

    assert settings.workos_api_key is None
    assert settings.scanner_service_token is None


def test_production_worker_profile_does_not_require_browser_auth_secrets():
    settings = Settings(
        environment="prod",
        service_role="worker",
        product_database_url="postgresql+psycopg://archagent:test@db/archagent",
        postgres_dsn="postgresql+psycopg://archagent:test@db/archagent",
        storage_backend="gcs",
        document_scan_mode="clamav",
        gcp_storage_bucket="knowledge",
        gcp_quarantine_bucket="quarantine",
        gcp_tasks_service_account="tasks@test-project.iam.gserviceaccount.com",
        gcp_tasks_oidc_audience="https://worker.example.test",
        scanner_service_url="https://scanner.example.test",
        scanner_service_token="scanner-token",
        workos_api_key="api-key",
        workos_client_id="client-id",
        workos_cookie_password=None,
        csrf_secret=None,
        allowed_origin_regex="",
    )

    assert settings.workos_cookie_password is None
    assert settings.csrf_secret is None
