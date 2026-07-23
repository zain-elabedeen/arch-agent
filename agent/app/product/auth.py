"""Authentication adapters and FastAPI authorization dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fastapi import Depends, HTTPException, Request

from agent.app.config import Settings, get_settings
from agent.app.product.store import (
    APP_ROLES,
    DEV_ORG_ID,
    DEV_OWNER_ID,
    DEV_STAFF_ID,
    DEV_VIEWER_ID,
    ProductStore,
    get_product_store,
    normalize_workos_role,
)
from agent.app.product.workos_client import get_field, get_workos_client

SESSION_COOKIE = "__Host-archagent-session"


@dataclass(frozen=True)
class Identity:
    user_id: str
    email: str
    name: str
    organization_id: str | None
    role: str | None
    is_internal: bool = False
    permissions: tuple[str, ...] = ()
    workos_user_id: str | None = None
    workos_organization_id: str | None = None


class AuthProvider(Protocol):
    def authenticate(self, request: Request, store: ProductStore) -> Identity: ...


class DevAuthProvider:
    """Seeded local identities selected with ``X-ArchAgent-User``."""

    def authenticate(self, request: Request, store: ProductStore) -> Identity:
        requested = request.headers.get("x-archagent-user", "owner").lower()
        user_id = {
            "owner": DEV_OWNER_ID,
            "viewer": DEV_VIEWER_ID,
            "staff": DEV_STAFF_ID,
        }.get(requested, requested)
        user = store.get_user(user_id)
        if not user:
            raise HTTPException(status_code=401, detail="Unknown development user.")
        if user["is_internal"]:
            return Identity(user["id"], user["email"], user["name"], None, None, True)
        organization_id = request.headers.get("x-archagent-organization", DEV_ORG_ID)
        membership = store.membership(user["id"], organization_id)
        if not membership:
            raise HTTPException(status_code=403, detail="User is not a member of this organization.")
        return Identity(user["id"], user["email"], user["name"], organization_id, membership["role"], False)


def _display_name(user: object) -> str:
    first = str(get_field(user, "first_name", "firstName", default="") or "").strip()
    last = str(get_field(user, "last_name", "lastName", default="") or "").strip()
    return " ".join(part for part in (first, last) if part) or str(get_field(user, "email", default=""))


def identity_from_workos_response(response: object, store: ProductStore, settings: Settings) -> Identity:
    user = get_field(response, "user")
    if not user:
        raise HTTPException(status_code=401, detail="WorkOS session has no user.")
    workos_user_id = str(get_field(user, "id"))
    workos_organization_id = get_field(response, "organization_id", "organizationId")
    role = normalize_workos_role(get_field(response, "role_slug", "roleSlug", "role"))
    roles_role = normalize_workos_role(get_field(response, "roles"))
    if role not in APP_ROLES and roles_role in APP_ROLES:
        role = roles_role
    permissions = tuple(str(item) for item in (get_field(response, "permissions", default=[]) or []))
    local = store.upsert_workos_identity(
        workos_user_id=workos_user_id,
        email=str(get_field(user, "email")),
        name=_display_name(user),
        workos_organization_id=str(workos_organization_id) if workos_organization_id else None,
        role=role,
        is_internal=workos_user_id in settings.internal_user_id_set,
    )
    return Identity(
        user_id=local["user_id"],
        email=local["email"],
        name=local["name"],
        organization_id=local.get("organization_id"),
        role=local.get("role"),
        is_internal=bool(local["is_internal"]),
        permissions=permissions,
        workos_user_id=workos_user_id,
        workos_organization_id=str(workos_organization_id) if workos_organization_id else None,
    )


def ensure_self_serve_organization(
    identity: Identity,
    store: ProductStore,
    settings: Settings,
    client: object | None = None,
) -> Identity:
    if (
        not settings.workos_auto_provision_signups
        or identity.is_internal
        or identity.organization_id
        or not identity.workos_user_id
    ):
        return identity
    workos_client = client or get_workos_client()
    provisioned = workos_client.provision_self_serve_organization(
        workos_user_id=identity.workos_user_id,
        email=identity.email,
        name=identity.name,
    )
    local = store.upsert_workos_identity(
        workos_user_id=identity.workos_user_id,
        email=identity.email,
        name=identity.name,
        workos_organization_id=str(provisioned["organization_id"]),
        role=normalize_workos_role(provisioned.get("role")) or "owner",
        is_internal=False,
        organization_name=str(provisioned.get("organization_name") or provisioned["organization_id"]),
        workos_membership_id=str(provisioned["membership_id"]) if provisioned.get("membership_id") else None,
    )
    return Identity(
        user_id=local["user_id"],
        email=local["email"],
        name=local["name"],
        organization_id=local.get("organization_id"),
        role=local.get("role"),
        is_internal=bool(local["is_internal"]),
        permissions=identity.permissions,
        workos_user_id=identity.workos_user_id,
        workos_organization_id=str(provisioned["organization_id"]),
    )


class WorkOSAuthProvider:
    """Authenticate sealed WorkOS AuthKit sessions and rotate refreshed cookies."""

    def __init__(self, settings: Settings):
        if not settings.workos_api_key or not settings.workos_client_id or not settings.workos_cookie_password:
            raise RuntimeError(
                "WorkOS auth requires ARCHAGENT_WORKOS_API_KEY, ARCHAGENT_WORKOS_CLIENT_ID, "
                "and ARCHAGENT_WORKOS_COOKIE_PASSWORD."
            )
        self.settings = settings

    def authenticate(self, request: Request, store: ProductStore) -> Identity:
        sealed_session = request.cookies.get(SESSION_COOKIE)
        if not sealed_session:
            raise HTTPException(status_code=401, detail="Authentication required.")
        session = get_workos_client().load_session(sealed_session)
        response = session.authenticate()
        if not get_field(response, "authenticated", default=False):
            if get_field(response, "reason") == "no_session_cookie_provided":
                raise HTTPException(status_code=401, detail="Authentication required.")
            response = session.refresh()
            if not get_field(response, "authenticated", default=False):
                raise HTTPException(status_code=401, detail="Authentication required.")
            request.state.archagent_rotated_session = get_field(response, "sealed_session", "sealedSession")
        identity = identity_from_workos_response(response, store, self.settings)
        return ensure_self_serve_organization(identity, store, self.settings)


def get_auth_provider() -> AuthProvider:
    settings = get_settings()
    if settings.auth_mode == "workos":
        return WorkOSAuthProvider(settings)
    return DevAuthProvider()


def require_identity(request: Request, store: ProductStore = Depends(get_product_store)) -> Identity:
    return get_auth_provider().authenticate(request, store)


def require_customer(identity: Identity = Depends(require_identity)) -> Identity:
    if identity.is_internal or not identity.organization_id:
        raise HTTPException(status_code=403, detail="Customer organization membership required.")
    return identity


def require_roles(*roles: str):
    def dependency(identity: Identity = Depends(require_customer)) -> Identity:
        if identity.role not in roles:
            raise HTTPException(status_code=403, detail=f"Required role: {', '.join(roles)}.")
        return identity

    return dependency


def require_staff(identity: Identity = Depends(require_identity)) -> Identity:
    if not identity.is_internal:
        raise HTTPException(status_code=403, detail="Internal staff access required.")
    return identity
