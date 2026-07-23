"""Tenant-aware product persistence used by local APIs and scoped RAG."""

from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from threading import Lock
from typing import Any, Iterable

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import JSON, Column, DateTime, String, and_, create_engine, delete, func, insert, inspect, or_, select, text, update
from sqlalchemy.engine import Engine

from agent.app.config import Settings, get_settings
from agent.app.knowledge.models import KnowledgeChunkReference
from agent.app.product import schema

DEV_ORG_ID = "org_dev_archagent"
DEV_CLUSTER_ID = "cluster_dev_local"
DEV_OWNER_ID = "user_dev_owner"
DEV_VIEWER_ID = "user_dev_viewer"
DEV_STAFF_ID = "user_dev_staff"
APP_ROLE_PRIORITY = ("owner", "admin", "viewer")
APP_ROLES = set(APP_ROLE_PRIORITY)
WORKOS_ROLE_FIELDS = ("slug", "role_slug", "roleSlug", "role")
_schema_init_lock = Lock()


def _require_product_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    missing = [table_name for table_name in schema.metadata.tables if not inspector.has_table(table_name)]
    if missing:
        raise RuntimeError(
            "Product schema is not migrated. Run `.venv/bin/alembic upgrade head`. "
            f"Missing tables: {', '.join(missing)}"
        )


def _ensure_updated_at_columns(engine: Engine) -> None:
    """Upgrade local create-all databases that predate the updated timestamps."""
    with engine.begin() as conn:
        operations = Operations(MigrationContext.configure(conn))
        for table_name in schema.metadata.tables:
            columns = {column["name"] for column in inspect(conn).get_columns(table_name)}
            if "updated_at" in columns:
                continue
            with operations.batch_alter_table(table_name) as batch_op:
                batch_op.add_column(Column("updated_at", DateTime(timezone=True), nullable=True))
            conn.execute(text(f'UPDATE "{table_name}" SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL'))
            with operations.batch_alter_table(table_name) as batch_op:
                batch_op.alter_column(
                    "updated_at",
                    existing_type=DateTime(timezone=True),
                    nullable=False,
                    server_default=func.now(),
                )


def _ensure_hosted_columns(engine: Engine) -> None:
    """Upgrade local create-all databases that predate hosted backend fields."""
    additions = {
        "organizations": [
            Column("status", String, nullable=True),
            Column("workos_organization_id", String, nullable=True),
            Column("workos_updated_at", DateTime(timezone=True), nullable=True),
        ],
        "users": [
            Column("status", String, nullable=True),
            Column("workos_user_id", String, nullable=True),
            Column("workos_updated_at", DateTime(timezone=True), nullable=True),
        ],
        "organization_memberships": [
            Column("status", String, nullable=True),
            Column("workos_membership_id", String, nullable=True),
            Column("workos_updated_at", DateTime(timezone=True), nullable=True),
        ],
        "organization_invitations": [
            Column("workos_invitation_id", String, nullable=True),
            Column("workos_updated_at", DateTime(timezone=True), nullable=True),
        ],
        "collector_credentials": [
            Column("purpose", String, nullable=True),
            Column("expires_at", DateTime(timezone=True), nullable=True),
            Column("used_at", DateTime(timezone=True), nullable=True),
        ],
        "snapshot_runs": [Column("snapshot", JSON, nullable=True)],
    }
    with engine.begin() as conn:
        operations = Operations(MigrationContext.configure(conn))
        for table_name, columns_to_add in additions.items():
            existing = {column["name"] for column in inspect(conn).get_columns(table_name)}
            for column in columns_to_add:
                if column.name in existing:
                    continue
                with operations.batch_alter_table(table_name) as batch_op:
                    batch_op.add_column(column)
        conn.execute(text("UPDATE organization_memberships SET status = 'active' WHERE status IS NULL"))
        conn.execute(text("UPDATE organizations SET status = 'active' WHERE status IS NULL"))
        conn.execute(text("UPDATE users SET status = 'active' WHERE status IS NULL"))
        conn.execute(text("UPDATE collector_credentials SET purpose = 'collector' WHERE purpose IS NULL"))
        conn.execute(text("UPDATE collector_credentials SET expires_at = CURRENT_TIMESTAMP WHERE expires_at IS NULL"))
        conn.execute(text("UPDATE snapshot_runs SET snapshot = '{}' WHERE snapshot IS NULL"))


def _ensure_nullable_invitation_inviter(engine: Engine) -> None:
    """Allow local mirrors of WorkOS invitations that were created externally."""
    with engine.begin() as conn:
        inviter = next(item for item in inspect(conn).get_columns("organization_invitations") if item["name"] == "invited_by_user_id")
        if inviter["nullable"]:
            return
        operations = Operations(MigrationContext.configure(conn))
        with operations.batch_alter_table("organization_invitations") as batch_op:
            batch_op.alter_column("invited_by_user_id", existing_type=String(), nullable=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _workos_timestamp(payload: dict[str, Any], data: dict[str, Any]) -> datetime:
    value = data.get("updated_at") or data.get("updatedAt") or payload.get("created_at") or payload.get("createdAt")
    if isinstance(value, datetime):
        parsed = value
    elif value:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    else:
        return utcnow()
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _is_stale(existing: dict[str, Any] | None, source_updated_at: datetime) -> bool:
    if not existing or not existing.get("workos_updated_at"):
        return False
    current = existing["workos_updated_at"]
    if not current.tzinfo:
        current = current.replace(tzinfo=timezone.utc)
    return source_updated_at <= current


def normalize_workos_role(role: Any) -> str | None:
    if role is None:
        return None
    if isinstance(role, (list, tuple, set, frozenset)):
        normalized_roles = [normalize_workos_role(item) for item in role]
        for app_role in APP_ROLE_PRIORITY:
            if app_role in normalized_roles:
                return app_role
        return next((item for item in normalized_roles if item), None)
    if isinstance(role, dict):
        for field in WORKOS_ROLE_FIELDS:
            nested = role.get(field)
            if nested:
                return normalize_workos_role(nested)
        return None
    for field in WORKOS_ROLE_FIELDS:
        nested = getattr(role, field, None)
        if nested:
            return normalize_workos_role(nested)
    return str(role).strip().lower()


def _local_role_from_workos(role: Any, existing_role: str | None = None) -> str:
    normalized = normalize_workos_role(role)
    if normalized in APP_ROLES:
        return normalized
    if existing_role in APP_ROLES:
        return existing_role
    return "viewer"


def _has_active_membership_for_email(conn: Any, organization_id: str, email: str) -> bool:
    normalized_email = email.lower().strip()
    if not normalized_email:
        return False
    return bool(
        conn.execute(
            select(schema.organization_memberships.c.user_id)
            .join(schema.users, schema.users.c.id == schema.organization_memberships.c.user_id)
            .where(
                and_(
                    schema.organization_memberships.c.organization_id == organization_id,
                    schema.organization_memberships.c.status == "active",
                    schema.users.c.email == normalized_email,
                )
            )
        ).first()
    )


def _accept_pending_invitations_for_email(conn: Any, organization_id: str, email: str, source_updated_at: datetime) -> None:
    normalized_email = email.lower().strip()
    if not normalized_email:
        return
    conn.execute(
        update(schema.organization_invitations)
        .where(
            and_(
                schema.organization_invitations.c.organization_id == organization_id,
                schema.organization_invitations.c.email == normalized_email,
                schema.organization_invitations.c.status == "pending",
            )
        )
        .values(status="accepted", workos_updated_at=source_updated_at)
    )


class ProductStore:
    def __init__(self, engine: Engine, settings: Settings):
        self.engine = engine
        self.settings = settings

    def ensure_schema(self) -> None:
        if self.engine.dialect.name == "postgresql":
            _require_product_schema(self.engine)
            if self.settings.environment != "prod":
                self.seed_dev_data(DEV_ORG_ID)
            return
        if self.settings.environment == "prod":
            return
        with _schema_init_lock:
            schema.metadata.create_all(self.engine)
            _ensure_hosted_columns(self.engine)
            _ensure_updated_at_columns(self.engine)
            _ensure_nullable_invitation_inviter(self.engine)
            self.seed_dev_data(DEV_ORG_ID)

    @contextmanager
    def _tenant_transaction(self, organization_id: str | None = None, *, internal: bool = False):
        with self.engine.begin() as conn:
            if self.engine.dialect.name == "postgresql":
                conn.execute(
                    text("SELECT set_config('archagent.organization_id', :organization_id, true)"),
                    {"organization_id": organization_id or ""},
                )
                conn.execute(
                    text("SELECT set_config('archagent.internal', :internal, true)"),
                    {"internal": "true" if internal else "false"},
                )
            yield conn

    def seed_dev_data(self, organization_id: str | None = None) -> None:
        now = utcnow()
        with self._tenant_transaction(organization_id) as conn:
            if conn.execute(select(schema.organizations.c.id).where(schema.organizations.c.id == DEV_ORG_ID)).first():
                return
            conn.execute(insert(schema.organizations), [{"id": DEV_ORG_ID, "name": "ArchAgent Demo", "slug": "archagent-demo", "created_at": now}])
            conn.execute(
                insert(schema.users),
                [
                    {"id": DEV_OWNER_ID, "email": "owner@archagent.local", "name": "Dev Owner", "is_internal": False, "created_at": now},
                    {"id": DEV_VIEWER_ID, "email": "viewer@archagent.local", "name": "Dev Viewer", "is_internal": False, "created_at": now},
                    {"id": DEV_STAFF_ID, "email": "staff@archagent.local", "name": "ArchAgent Staff", "is_internal": True, "created_at": now},
                ],
            )
            conn.execute(
                insert(schema.organization_memberships),
                [
                    {"organization_id": DEV_ORG_ID, "user_id": DEV_OWNER_ID, "role": "owner", "created_at": now},
                    {"organization_id": DEV_ORG_ID, "user_id": DEV_VIEWER_ID, "role": "viewer", "created_at": now},
                ],
            )
            conn.execute(
                insert(schema.clusters),
                {
                    "id": DEV_CLUSTER_ID,
                    "organization_id": DEV_ORG_ID,
                    "name": "Local Kubernetes",
                    "environment": "development",
                    "connection_mode": "local_kubeconfig",
                    "collector_status": "local",
                    "created_at": now,
                },
            )
            conn.execute(
                insert(schema.cluster_namespaces),
                [
                    {"cluster_id": DEV_CLUSTER_ID, "namespace": "default", "monitored": True, "is_system": False, "created_at": now},
                    {"cluster_id": DEV_CLUSTER_ID, "namespace": "kube-system", "monitored": False, "is_system": True, "created_at": now},
                ],
            )

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(select(schema.users).where(schema.users.c.id == user_id)).mappings().first()
            return dict(row) if row else None

    def upsert_workos_identity(
        self,
        *,
        workos_user_id: str,
        email: str,
        name: str,
        workos_organization_id: str | None,
        role: Any,
        is_internal: bool,
        organization_name: str | None = None,
        workos_membership_id: str | None = None,
    ) -> dict[str, Any]:
        """Mirror the claims needed for local authorization from a WorkOS session."""
        now = utcnow()
        normalized_email = email.lower().strip()
        with self.engine.begin() as conn:
            user = conn.execute(
                select(schema.users).where(schema.users.c.workos_user_id == workos_user_id)
            ).mappings().first()
            if not user:
                user = conn.execute(select(schema.users).where(schema.users.c.email == normalized_email)).mappings().first()
            if not user:
                user_id = new_id("user")
                conn.execute(
                    insert(schema.users),
                    {
                        "id": user_id,
                        "email": normalized_email,
                        "name": name,
                        "is_internal": is_internal,
                        "status": "active",
                        "workos_user_id": workos_user_id,
                        "created_at": now,
                    },
                )
            else:
                user_id = str(user["id"])
                conn.execute(
                    update(schema.users)
                    .where(schema.users.c.id == user_id)
                    .values(
                        email=normalized_email,
                        name=name,
                        is_internal=is_internal,
                        status="active",
                        workos_user_id=workos_user_id,
                    )
                )
            if not workos_organization_id:
                membership = conn.execute(
                    select(
                        schema.organization_memberships.c.organization_id,
                        schema.organization_memberships.c.role,
                    )
                    .where(
                        and_(
                            schema.organization_memberships.c.user_id == user_id,
                            schema.organization_memberships.c.status == "active",
                        )
                    )
                    .order_by(schema.organization_memberships.c.created_at)
                ).mappings().first()
                return {
                    "user_id": user_id,
                    "email": normalized_email,
                    "name": name,
                    "organization_id": str(membership["organization_id"]) if membership else None,
                    "role": str(membership["role"]) if membership else None,
                    "is_internal": is_internal,
                }
            organization = conn.execute(
                select(schema.organizations).where(schema.organizations.c.workos_organization_id == workos_organization_id)
            ).mappings().first()
            if not organization:
                organization_id = new_id("org")
                slug = f"workos-{workos_organization_id.lower()}"
                local_organization_name = (organization_name or workos_organization_id).strip() or workos_organization_id
                conn.execute(
                    insert(schema.organizations),
                    {
                        "id": organization_id,
                        "name": local_organization_name,
                        "slug": slug,
                        "status": "active",
                        "workos_organization_id": workos_organization_id,
                        "created_at": now,
                    },
                )
            else:
                organization_id = str(organization["id"])
                values = {"status": "active"}
                if organization_name:
                    values["name"] = organization_name
                conn.execute(update(schema.organizations).where(schema.organizations.c.id == organization_id).values(**values))
            membership = conn.execute(
                select(schema.organization_memberships).where(
                    and_(
                        schema.organization_memberships.c.organization_id == organization_id,
                        schema.organization_memberships.c.user_id == user_id,
                    )
                )
            ).mappings().first()
            existing_role = str(membership["role"]) if membership else None
            values = {"role": _local_role_from_workos(role, existing_role), "status": "active"}
            if workos_membership_id:
                values["workos_membership_id"] = workos_membership_id
            if membership:
                conn.execute(
                    update(schema.organization_memberships)
                    .where(
                        and_(
                            schema.organization_memberships.c.organization_id == organization_id,
                            schema.organization_memberships.c.user_id == user_id,
                        )
                    )
                    .values(**values)
                )
            else:
                conn.execute(
                    insert(schema.organization_memberships),
                    {
                        "organization_id": organization_id,
                        "user_id": user_id,
                        "created_at": now,
                        **values,
                    },
                )
        return {
            "user_id": user_id,
            "email": normalized_email,
            "name": name,
            "organization_id": organization_id,
            "role": values["role"],
            "is_internal": is_internal,
        }

    def get_workos_organization_id(self, organization_id: str) -> str | None:
        with self.engine.begin() as conn:
            return conn.execute(
                select(schema.organizations.c.workos_organization_id).where(schema.organizations.c.id == organization_id)
            ).scalar_one_or_none()

    def get_workos_user_id(self, user_id: str) -> str | None:
        with self.engine.begin() as conn:
            return conn.execute(select(schema.users.c.workos_user_id).where(schema.users.c.id == user_id)).scalar_one_or_none()

    def membership(self, user_id: str, organization_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                select(schema.organization_memberships).where(
                    and_(
                        schema.organization_memberships.c.user_id == user_id,
                        schema.organization_memberships.c.organization_id == organization_id,
                        schema.organization_memberships.c.status == "active",
                    )
                )
            ).mappings().first()
            return dict(row) if row else None

    def list_team(self, organization_id: str) -> list[dict[str, Any]]:
        with self._tenant_transaction(organization_id) as conn:
            rows = conn.execute(
                select(
                    schema.users.c.id,
                    schema.users.c.email,
                    schema.users.c.name,
                    schema.organization_memberships.c.role,
                )
                .join(schema.organization_memberships, schema.organization_memberships.c.user_id == schema.users.c.id)
                .where(schema.organization_memberships.c.organization_id == organization_id)
                .order_by(schema.users.c.email)
            ).mappings()
            return [dict(row) for row in rows]

    def list_invitations(self, organization_id: str) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    select(schema.organization_invitations)
                    .where(schema.organization_invitations.c.organization_id == organization_id)
                    .order_by(schema.organization_invitations.c.created_at.desc())
                ).mappings()
            ]

    def create_invitation(
        self,
        organization_id: str,
        actor_user_id: str,
        email: str,
        role: str,
        *,
        workos_invitation_id: str | None = None,
    ) -> dict[str, Any]:
        invitation = {
            "id": new_id("inv"),
            "organization_id": organization_id,
            "email": email.lower().strip(),
            "role": role,
            "status": "pending",
            "workos_invitation_id": workos_invitation_id,
            "invited_by_user_id": actor_user_id,
            "created_at": utcnow(),
        }
        with self.engine.begin() as conn:
            conn.execute(insert(schema.organization_invitations), invitation)
        self.audit(actor_user_id, organization_id, "team.invitation.created", "invitation", invitation["id"], {"email": invitation["email"], "role": role})
        return invitation

    def revoke_invitation(self, organization_id: str, actor_user_id: str, invitation_id: str) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(
                update(schema.organization_invitations)
                .where(
                    and_(
                        schema.organization_invitations.c.id == invitation_id,
                        schema.organization_invitations.c.organization_id == organization_id,
                    )
                )
                .values(status="revoked")
            )
        if result.rowcount:
            self.audit(actor_user_id, organization_id, "team.invitation.revoked", "invitation", invitation_id)
        return bool(result.rowcount)

    def get_membership(self, organization_id: str, membership_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                select(schema.organization_memberships).where(
                    and_(
                        schema.organization_memberships.c.organization_id == organization_id,
                        or_(
                            schema.organization_memberships.c.workos_membership_id == membership_id,
                            schema.organization_memberships.c.user_id == membership_id,
                        ),
                    )
                )
            ).mappings().first()
        return dict(row) if row else None

    def update_membership_role(self, organization_id: str, actor_user_id: str, membership_id: str, role: str) -> dict[str, Any]:
        membership = self.get_membership(organization_id, membership_id)
        if not membership:
            raise LookupError("membership_not_found")
        with self.engine.begin() as conn:
            conn.execute(
                update(schema.organization_memberships)
                .where(
                    and_(
                        schema.organization_memberships.c.organization_id == organization_id,
                        schema.organization_memberships.c.user_id == membership["user_id"],
                    )
                )
                .values(role=role)
            )
        self.audit(actor_user_id, organization_id, "team.membership.role.updated", "membership", membership_id, {"role": role})
        return self.get_membership(organization_id, membership_id) or {}

    def deactivate_membership(self, organization_id: str, actor_user_id: str, membership_id: str) -> bool:
        membership = self.get_membership(organization_id, membership_id)
        if not membership:
            return False
        with self.engine.begin() as conn:
            conn.execute(
                update(schema.organization_memberships)
                .where(
                    and_(
                        schema.organization_memberships.c.organization_id == organization_id,
                        schema.organization_memberships.c.user_id == membership["user_id"],
                    )
                )
                .values(status="inactive")
            )
        self.audit(actor_user_id, organization_id, "team.membership.deactivated", "membership", membership_id)
        return True

    def get_invitation(self, organization_id: str, invitation_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                select(schema.organization_invitations).where(
                    and_(
                        schema.organization_invitations.c.id == invitation_id,
                        schema.organization_invitations.c.organization_id == organization_id,
                    )
                )
            ).mappings().first()
            return dict(row) if row else None

    def list_clusters(self, organization_id: str) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(schema.clusters)
                .where(schema.clusters.c.organization_id == organization_id)
                .order_by(schema.clusters.c.name)
            ).mappings()
            return [dict(row) for row in rows]

    def create_cluster(self, organization_id: str, actor_user_id: str, name: str, environment: str) -> dict[str, Any]:
        cluster = {
            "id": new_id("cluster"),
            "organization_id": organization_id,
            "name": name.strip(),
            "environment": environment,
            "connection_mode": "helm",
            "collector_status": "pending",
            "created_at": utcnow(),
        }
        with self.engine.begin() as conn:
            conn.execute(insert(schema.clusters), cluster)
        self.audit(actor_user_id, organization_id, "cluster.created", "cluster", cluster["id"], {"name": name})
        return cluster

    def create_collector_registration_token(self, organization_id: str, actor_user_id: str, cluster_id: str) -> str:
        if not self.get_cluster(organization_id, cluster_id):
            raise LookupError("cluster_not_found")
        token = secrets.token_urlsafe(32)
        credential = {
            "id": new_id("collector"),
            "organization_id": organization_id,
            "cluster_id": cluster_id,
            "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "purpose": "registration",
            "revoked": False,
            "expires_at": utcnow() + timedelta(seconds=self.settings.collector_registration_ttl_sec),
            "created_at": utcnow(),
        }
        with self.engine.begin() as conn:
            conn.execute(
                update(schema.collector_credentials)
                .where(
                    and_(
                        schema.collector_credentials.c.organization_id == organization_id,
                        schema.collector_credentials.c.cluster_id == cluster_id,
                        schema.collector_credentials.c.revoked.is_(False),
                    )
                )
                .values(revoked=True)
            )
            conn.execute(insert(schema.collector_credentials), credential)
        self.audit(actor_user_id, organization_id, "cluster.registration_token.created", "cluster", cluster_id)
        return token

    def exchange_collector_registration_token(self, token: str) -> dict[str, Any]:
        now = utcnow()
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.engine.begin() as conn:
            registration = conn.execute(
                select(schema.collector_credentials).where(
                    and_(
                        schema.collector_credentials.c.token_hash == token_hash,
                        schema.collector_credentials.c.purpose == "registration",
                        schema.collector_credentials.c.revoked.is_(False),
                        schema.collector_credentials.c.used_at.is_(None),
                        schema.collector_credentials.c.expires_at > now,
                    )
                )
            ).mappings().first()
            if not registration:
                raise LookupError("invalid_registration_token")
            credential_token = secrets.token_urlsafe(48)
            credential = {
                "id": new_id("collector"),
                "organization_id": registration["organization_id"],
                "cluster_id": registration["cluster_id"],
                "token_hash": hashlib.sha256(credential_token.encode("utf-8")).hexdigest(),
                "purpose": "collector",
                "revoked": False,
                "expires_at": now + timedelta(seconds=self.settings.collector_credential_ttl_sec),
                "created_at": now,
            }
            conn.execute(
                update(schema.collector_credentials)
                .where(schema.collector_credentials.c.id == registration["id"])
                .values(revoked=True, used_at=now)
            )
            conn.execute(insert(schema.collector_credentials), credential)
            conn.execute(
                update(schema.clusters)
                .where(schema.clusters.c.id == registration["cluster_id"])
                .values(collector_status="registered")
            )
        return {
            "credential": credential_token,
            "expires_at": credential["expires_at"],
            "organization_id": credential["organization_id"],
            "cluster_id": credential["cluster_id"],
        }

    def authenticate_collector(self, token: str) -> dict[str, Any]:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.engine.begin() as conn:
            row = conn.execute(
                select(schema.collector_credentials).where(
                    and_(
                        schema.collector_credentials.c.token_hash == token_hash,
                        schema.collector_credentials.c.purpose == "collector",
                        schema.collector_credentials.c.revoked.is_(False),
                        schema.collector_credentials.c.expires_at > utcnow(),
                    )
                )
            ).mappings().first()
        if not row:
            raise LookupError("invalid_collector_credential")
        return dict(row)

    def rotate_collector_credential(self, token: str) -> dict[str, Any]:
        return self.rotate_authenticated_collector_credential(self.authenticate_collector(token))

    def rotate_authenticated_collector_credential(self, current: dict[str, Any]) -> dict[str, Any]:
        now = utcnow()
        new_token = secrets.token_urlsafe(48)
        credential = {
            "id": new_id("collector"),
            "organization_id": current["organization_id"],
            "cluster_id": current["cluster_id"],
            "token_hash": hashlib.sha256(new_token.encode("utf-8")).hexdigest(),
            "purpose": "collector",
            "revoked": False,
            "expires_at": now + timedelta(seconds=self.settings.collector_credential_ttl_sec),
            "created_at": now,
        }
        with self.engine.begin() as conn:
            conn.execute(update(schema.collector_credentials).where(schema.collector_credentials.c.id == current["id"]).values(revoked=True))
            conn.execute(insert(schema.collector_credentials), credential)
        return {"credential": new_token, "expires_at": credential["expires_at"]}

    def record_collector_heartbeat(self, credential: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        heartbeat = {
            "id": new_id("heartbeat"),
            "organization_id": credential["organization_id"],
            "cluster_id": credential["cluster_id"],
            "payload": payload,
            "created_at": utcnow(),
        }
        with self.engine.begin() as conn:
            conn.execute(insert(schema.cluster_heartbeats), heartbeat)
            conn.execute(
                update(schema.clusters)
                .where(schema.clusters.c.id == credential["cluster_id"])
                .values(collector_status="healthy", last_heartbeat_at=heartbeat["created_at"])
            )
        return heartbeat

    def store_collector_snapshot(self, credential: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        run = {
            "id": str(uuid.uuid4()),
            "organization_id": credential["organization_id"],
            "cluster_id": credential["cluster_id"],
            "source_run_id": str(snapshot.get("run_id") or "") or None,
            "snapshot": snapshot,
            "created_at": utcnow(),
        }
        workloads = []
        for item in snapshot.get("services") or []:
            workloads.append(
                {
                    "id": new_id("workload"),
                    "organization_id": credential["organization_id"],
                    "cluster_id": credential["cluster_id"],
                    "run_id": run["id"],
                    "namespace": str(item.get("namespace") or "default"),
                    "workload_kind": str(item.get("kind") or "Deployment"),
                    "workload_name": str(item.get("name") or ""),
                    "payload": item,
                }
            )
        edges = []
        for item in (snapshot.get("topology") or {}).get("edges") or []:
            edges.append(
                {
                    "id": new_id("edge"),
                    "organization_id": credential["organization_id"],
                    "cluster_id": credential["cluster_id"],
                    "run_id": run["id"],
                    "source": str(item.get("from") or item.get("from_service") or ""),
                    "target": str(item.get("to") or item.get("to_service") or ""),
                    "payload": item,
                }
            )
        with self._tenant_transaction(credential["organization_id"]) as conn:
            conn.execute(insert(schema.snapshot_runs), run)
            if workloads:
                conn.execute(insert(schema.workload_snapshots), workloads)
            if edges:
                conn.execute(insert(schema.topology_edges), edges)
        return run

    def load_latest_cluster_snapshot(self, organization_id: str, cluster_id: str) -> dict[str, Any] | None:
        with self._tenant_transaction(organization_id) as conn:
            row = conn.execute(
                select(schema.snapshot_runs)
                .where(
                    and_(
                        schema.snapshot_runs.c.organization_id == organization_id,
                        schema.snapshot_runs.c.cluster_id == cluster_id,
                    )
                )
                .order_by(schema.snapshot_runs.c.created_at.desc())
                .limit(1)
            ).mappings().first()
        return dict(row) if row else None

    def load_cluster_snapshot(self, organization_id: str, cluster_id: str, run_id: str) -> dict[str, Any] | None:
        with self._tenant_transaction(organization_id) as conn:
            row = conn.execute(
                select(schema.snapshot_runs).where(
                    and_(
                        schema.snapshot_runs.c.id == run_id,
                        schema.snapshot_runs.c.organization_id == organization_id,
                        schema.snapshot_runs.c.cluster_id == cluster_id,
                    )
                )
            ).mappings().first()
        return dict(row) if row else None

    def get_cluster(self, organization_id: str, cluster_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                select(schema.clusters).where(
                    and_(schema.clusters.c.id == cluster_id, schema.clusters.c.organization_id == organization_id)
                )
            ).mappings().first()
            return dict(row) if row else None

    def list_namespaces(self, organization_id: str, cluster_id: str) -> list[dict[str, Any]]:
        if not self.get_cluster(organization_id, cluster_id):
            return []
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(schema.cluster_namespaces)
                .where(schema.cluster_namespaces.c.cluster_id == cluster_id)
                .order_by(schema.cluster_namespaces.c.namespace)
            ).mappings()
            return [dict(row) for row in rows]

    def replace_namespaces(self, organization_id: str, actor_user_id: str, cluster_id: str, namespaces: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.get_cluster(organization_id, cluster_id):
            raise LookupError("cluster_not_found")
        now = utcnow()
        values = [
            {
                "cluster_id": cluster_id,
                "namespace": item["namespace"].strip(),
                "monitored": bool(item.get("monitored")),
                "is_system": bool(item.get("is_system")),
                "created_at": now,
            }
            for item in namespaces
            if item.get("namespace", "").strip()
        ]
        with self.engine.begin() as conn:
            conn.execute(delete(schema.cluster_namespaces).where(schema.cluster_namespaces.c.cluster_id == cluster_id))
            if values:
                conn.execute(insert(schema.cluster_namespaces), values)
        self.audit(actor_user_id, organization_id, "cluster.namespaces.updated", "cluster", cluster_id, {"namespaces": [item["namespace"] for item in values]})
        return self.list_namespaces(organization_id, cluster_id)

    def sync_discovered_namespaces(
        self,
        organization_id: str,
        cluster_id: str,
        discovered_namespaces: Iterable[str],
        excluded_namespaces: Iterable[str],
    ) -> list[dict[str, Any]]:
        """Keep local kubeconfig namespace choices aligned with collected snapshots."""
        if not self.get_cluster(organization_id, cluster_id):
            raise LookupError("cluster_not_found")
        discovered = {item.strip() for item in discovered_namespaces if item.strip()}
        excluded = {item.strip() for item in excluded_namespaces if item.strip()}
        existing = {item["namespace"]: item for item in self.list_namespaces(organization_id, cluster_id)}
        rows = []
        now = utcnow()
        for namespace in sorted(set(existing) | discovered | excluded):
            previous = existing.get(namespace)
            is_system = namespace in excluded or bool(previous and previous["is_system"])
            rows.append(
                {
                    "cluster_id": cluster_id,
                    "namespace": namespace,
                    "monitored": False if is_system else bool(previous["monitored"]) if previous else namespace in discovered,
                    "is_system": is_system,
                    "created_at": previous["created_at"] if previous else now,
                }
            )
        discovered_app_namespaces = discovered - excluded
        if discovered_app_namespaces and not any(
            row["monitored"] and row["namespace"] in discovered_app_namespaces for row in rows
        ):
            for row in rows:
                if row["namespace"] in discovered_app_namespaces:
                    row["monitored"] = True
        with self.engine.begin() as conn:
            conn.execute(delete(schema.cluster_namespaces).where(schema.cluster_namespaces.c.cluster_id == cluster_id))
            if rows:
                conn.execute(insert(schema.cluster_namespaces), rows)
        return self.list_namespaces(organization_id, cluster_id)

    def create_document(
        self,
        *,
        scope: str,
        organization_id: str | None,
        actor_user_id: str,
        title: str,
        filename: str,
        mime_type: str,
    ) -> dict[str, Any]:
        document_id = new_id("doc")
        document = {
            "id": document_id,
            "logical_document_id": document_id,
            "version": 1,
            "scope": scope,
            "organization_id": organization_id,
            "title": title.strip() or filename,
            "filename": filename,
            "mime_type": mime_type,
            "byte_size": 0,
            "object_key": f"{scope}/{organization_id or 'global'}/{document_id}/{filename}",
            "status": "uploading",
            "enabled": True,
            "uploaded_by_user_id": actor_user_id,
            "created_at": utcnow(),
        }
        with self._tenant_transaction(organization_id, internal=scope == "global") as conn:
            conn.execute(insert(schema.knowledge_documents), document)
        self.audit(actor_user_id, organization_id, "knowledge.document.created", "knowledge_document", document_id, {"scope": scope, "filename": filename})
        return document

    def get_document(
        self,
        document_id: str,
        *,
        organization_id: str | None = None,
        internal: bool = False,
    ) -> dict[str, Any] | None:
        with self._tenant_transaction(organization_id, internal=internal) as conn:
            row = conn.execute(select(schema.knowledge_documents).where(schema.knowledge_documents.c.id == document_id)).mappings().first()
            return dict(row) if row else None

    def list_documents(self, *, scope: str, organization_id: str | None = None) -> list[dict[str, Any]]:
        where = schema.knowledge_documents.c.scope == scope
        if scope == "organization":
            where = and_(where, schema.knowledge_documents.c.organization_id == organization_id)
        with self._tenant_transaction(organization_id, internal=scope == "global") as conn:
            rows = conn.execute(
                select(schema.knowledge_documents)
                .where(and_(where, schema.knowledge_documents.c.deleted_at.is_(None)))
                .order_by(schema.knowledge_documents.c.created_at.desc())
            ).mappings()
            return [dict(row) for row in rows]

    def update_document(
        self,
        document_id: str,
        *,
        organization_id: str | None = None,
        internal: bool = False,
        **values: Any,
    ) -> dict[str, Any] | None:
        with self._tenant_transaction(organization_id, internal=internal) as conn:
            conn.execute(update(schema.knowledge_documents).where(schema.knowledge_documents.c.id == document_id).values(**values))
        return self.get_document(document_id, organization_id=organization_id, internal=internal)

    def delete_document(self, document_id: str, actor_user_id: str, *, organization_id: str | None = None, internal: bool = False) -> None:
        doc = self.get_document(document_id, organization_id=organization_id, internal=internal)
        if not doc:
            return
        with self._tenant_transaction(organization_id, internal=internal) as conn:
            conn.execute(delete(schema.knowledge_chunks).where(schema.knowledge_chunks.c.document_id == document_id))
            conn.execute(update(schema.knowledge_documents).where(schema.knowledge_documents.c.id == document_id).values(enabled=False, status="deleted", deleted_at=utcnow()))
        self.audit(actor_user_id, doc.get("organization_id"), "knowledge.document.deleted", "knowledge_document", document_id)

    def create_ingestion_job(self, document_id: str) -> dict[str, Any]:
        job = {
            "id": new_id("ingest"),
            "document_id": document_id,
            "status": "processing",
            "created_at": utcnow(),
        }
        with self.engine.begin() as conn:
            conn.execute(insert(schema.knowledge_ingestion_jobs), job)
        return job

    def complete_ingestion_job(self, job_id: str, *, status: str, error_code: str | None = None) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(schema.knowledge_ingestion_jobs)
                .where(schema.knowledge_ingestion_jobs.c.id == job_id)
                .values(status=status, error_code=error_code, completed_at=utcnow())
            )

    def replace_chunks(
        self,
        document: dict[str, Any],
        chunks: Iterable[Any],
        *,
        embeddings: list[list[float]] | None = None,
    ) -> None:
        from agent.app.knowledge.embeddings import vector_literal

        chunks = list(chunks)
        if embeddings is not None and len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have the same length")
        with self._tenant_transaction(document["organization_id"], internal=document["scope"] == "global") as conn:
            conn.execute(delete(schema.knowledge_chunks).where(schema.knowledge_chunks.c.document_id == document["id"]))
            rows = [
                {
                    "id": new_id("chunk"),
                    "document_id": document["id"],
                    "scope": document["scope"],
                    "organization_id": document["organization_id"],
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                    "content_hash": chunk.content_hash,
                    "metadata": chunk.metadata,
                }
                for chunk in chunks
            ]
            if rows:
                conn.execute(insert(schema.knowledge_chunks), rows)
            if embeddings is not None and self.engine.dialect.name == "postgresql":
                for row, embedding in zip(rows, embeddings):
                    conn.execute(
                        text("UPDATE knowledge_chunks SET embedding = CAST(:embedding AS vector) WHERE id = :id"),
                        {"id": row["id"], "embedding": vector_literal(embedding)},
                    )

    def search_knowledge(self, organization_id: str, query: str, *, top_k: int = 8) -> list[KnowledgeChunkReference]:
        if self.engine.dialect.name == "postgresql" and self.settings.rag_enabled:
            return self._search_knowledge_vector(organization_id, query, top_k=top_k)
        terms = {term for term in re.findall(r"[a-z0-9_-]+", query.lower()) if len(term) > 2}
        eligible = or_(
            and_(
                schema.knowledge_documents.c.scope == "organization",
                schema.knowledge_documents.c.organization_id == organization_id,
                schema.knowledge_documents.c.status == "ready",
                schema.knowledge_documents.c.enabled.is_(True),
            ),
            and_(
                schema.knowledge_documents.c.scope == "global",
                schema.knowledge_documents.c.status == "published",
                schema.knowledge_documents.c.enabled.is_(True),
            ),
        )
        with self._tenant_transaction(organization_id) as conn:
            rows = conn.execute(
                select(
                    schema.knowledge_chunks.c.id,
                    schema.knowledge_chunks.c.content,
                    schema.knowledge_chunks.c.metadata,
                    schema.knowledge_documents.c.id.label("document_id"),
                    schema.knowledge_documents.c.title,
                    schema.knowledge_documents.c.filename,
                    schema.knowledge_documents.c.scope,
                )
                .join(schema.knowledge_documents, schema.knowledge_documents.c.id == schema.knowledge_chunks.c.document_id)
                .where(eligible)
            ).mappings()
            scored = []
            for row in rows:
                content_terms = set(re.findall(r"[a-z0-9_-]+", str(row["content"]).lower()))
                score = len(terms & content_terms) / max(1, len(terms))
                if score > 0:
                    scored.append((score, row))
            scored.sort(key=lambda item: item[0], reverse=True)
            return [
                KnowledgeChunkReference(
                    source_title=str(row["title"]),
                    source_type="uploaded_document",
                    path=str(row["filename"]),
                    content=str(row["content"]),
                    score=float(score),
                    metadata={
                        **(row["metadata"] or {}),
                        "chunk_id": str(row["id"]),
                        "document_id": str(row["document_id"]),
                        "scope": str(row["scope"]),
                    },
                )
                for score, row in scored[:top_k]
            ]

    def _search_knowledge_vector(self, organization_id: str, query: str, *, top_k: int) -> list[KnowledgeChunkReference]:
        from agent.app.knowledge.embeddings import get_embedding_provider, vector_literal

        embedding = get_embedding_provider(self.settings).embed_query(query)
        statement = text(
            """
            WITH eligible_chunks AS (
                SELECT
                    c.id,
                    c.content,
                    c.metadata,
                    c.embedding,
                    d.id AS document_id,
                    d.title,
                    d.filename,
                    d.scope
                FROM knowledge_chunks c
                JOIN knowledge_documents d ON d.id = c.document_id
                WHERE c.embedding IS NOT NULL
                  AND (
                    (
                      d.scope = 'organization'
                      AND d.organization_id = :organization_id
                      AND d.status = 'ready'
                      AND d.enabled = TRUE
                    )
                    OR (
                      d.scope = 'global'
                      AND d.status = 'published'
                      AND d.enabled = TRUE
                    )
                  )
            )
            SELECT
                id,
                content,
                metadata,
                document_id,
                title,
                filename,
                scope,
                1 - (embedding <=> CAST(:embedding AS vector)) AS score
            FROM eligible_chunks
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :top_k
            """
        )
        with self._tenant_transaction(organization_id) as conn:
            rows = conn.execute(
                statement,
                {
                    "organization_id": organization_id,
                    "embedding": vector_literal(embedding),
                    "top_k": top_k,
                },
            ).mappings()
            return [
                KnowledgeChunkReference(
                    source_title=str(row["title"]),
                    source_type="uploaded_document",
                    path=str(row["filename"]),
                    content=str(row["content"]),
                    score=float(row["score"]),
                    metadata={
                        **(row["metadata"] or {}),
                        "chunk_id": str(row["id"]),
                        "document_id": str(row["document_id"]),
                        "scope": str(row["scope"]),
                    },
                )
                for row in rows
            ]

    def create_analysis_run(self, organization_id: str, actor_user_id: str, cluster_id: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        run = {
            "id": new_id("analysis"),
            "organization_id": organization_id,
            "cluster_id": cluster_id,
            "status": "queued",
            "input_payload": payload,
            "knowledge_chunk_ids": [],
            "created_by_user_id": actor_user_id,
            "created_at": utcnow(),
        }
        with self._tenant_transaction(organization_id) as conn:
            conn.execute(insert(schema.analysis_runs), run)
        self.audit(actor_user_id, organization_id, "analysis_run.created", "analysis_run", run["id"])
        return run

    def get_analysis_run(self, organization_id: str, run_id: str) -> dict[str, Any] | None:
        with self._tenant_transaction(organization_id) as conn:
            row = conn.execute(
                select(schema.analysis_runs).where(
                    and_(schema.analysis_runs.c.id == run_id, schema.analysis_runs.c.organization_id == organization_id)
                )
            ).mappings().first()
            return dict(row) if row else None

    def list_analysis_runs(self, organization_id: str, cluster_id: str | None = None) -> list[dict[str, Any]]:
        where = schema.analysis_runs.c.organization_id == organization_id
        if cluster_id:
            where = and_(where, schema.analysis_runs.c.cluster_id == cluster_id)
        with self._tenant_transaction(organization_id) as conn:
            rows = conn.execute(
                select(schema.analysis_runs)
                .where(where)
                .order_by(schema.analysis_runs.c.created_at.desc())
            ).mappings()
            return [dict(row) for row in rows]

    def complete_analysis_run(
        self,
        organization_id: str,
        run_id: str,
        *,
        status: str,
        result_payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        with self._tenant_transaction(organization_id) as conn:
            conn.execute(
                update(schema.analysis_runs)
                .where(
                    and_(
                        schema.analysis_runs.c.id == run_id,
                        schema.analysis_runs.c.organization_id == organization_id,
                    )
                )
                .values(status=status, result_payload=result_payload, completed_at=utcnow())
            )
        return self.get_analysis_run(organization_id, run_id)

    def list_accounts(self) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            accounts = [dict(row) for row in conn.execute(select(schema.organizations).order_by(schema.organizations.c.name)).mappings()]
        for account in accounts:
            account["users"] = len(self.list_team(account["id"]))
            account["clusters"] = len(self.list_clusters(account["id"]))
            account["documents"] = len(self.list_documents(scope="organization", organization_id=account["id"]))
        return accounts

    def get_account_details(self, organization_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                select(schema.organizations).where(schema.organizations.c.id == organization_id)
            ).mappings().first()
        if not row:
            return None

        account = dict(row)
        clusters = self.list_clusters(organization_id)
        for cluster in clusters:
            cluster["namespaces"] = self.list_namespaces(organization_id, cluster["id"])
        account.update(
            {
                "members": self.list_memberships(organization_id=organization_id),
                "invitations": self.list_invitations(organization_id),
                "clusters_detail": clusters,
                "documents_detail": self.list_documents(scope="organization", organization_id=organization_id),
                "analysis_runs": self.list_analysis_runs(organization_id),
                "audit_events": self.list_audit(organization_id),
            }
        )
        account["users"] = len(account["members"])
        account["clusters"] = len(account["clusters_detail"])
        account["documents"] = len(account["documents_detail"])
        return account

    def list_memberships(
        self,
        *,
        organization_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        statement = (
            select(
                schema.organization_memberships.c.organization_id,
                schema.organizations.c.name.label("organization_name"),
                schema.organizations.c.slug.label("organization_slug"),
                schema.organization_memberships.c.user_id,
                schema.users.c.email,
                schema.users.c.name.label("user_name"),
                schema.users.c.is_internal,
                schema.organization_memberships.c.role,
                schema.organization_memberships.c.created_at,
                schema.organization_memberships.c.updated_at,
            )
            .join(schema.organizations, schema.organizations.c.id == schema.organization_memberships.c.organization_id)
            .join(schema.users, schema.users.c.id == schema.organization_memberships.c.user_id)
        )
        if organization_id:
            statement = statement.where(schema.organization_memberships.c.organization_id == organization_id)
        if user_id:
            statement = statement.where(schema.organization_memberships.c.user_id == user_id)
        with self.engine.begin() as conn:
            rows = conn.execute(statement.order_by(schema.organizations.c.name, schema.users.c.email)).mappings()
            return [dict(row) for row in rows]

    def list_users(self) -> list[dict[str, Any]]:
        with self.engine.begin() as conn:
            users = [dict(row) for row in conn.execute(select(schema.users).order_by(schema.users.c.email)).mappings()]
        for user in users:
            user["memberships"] = self.list_memberships(user_id=user["id"])
        return users

    def list_audit(self, organization_id: str) -> list[dict[str, Any]]:
        with self._tenant_transaction(organization_id, internal=organization_id is None) as conn:
            return [
                dict(row)
                for row in conn.execute(
                    select(schema.audit_events)
                    .where(schema.audit_events.c.organization_id == organization_id)
                    .order_by(schema.audit_events.c.created_at.desc())
                    .limit(100)
                ).mappings()
            ]

    def record_workos_event(self, event_id: str, event_type: str, payload: dict[str, Any]) -> bool:
        """Persist a webhook receipt before enqueueing; return False for duplicates."""
        with self.engine.begin() as conn:
            if conn.execute(select(schema.workos_event_receipts.c.id).where(schema.workos_event_receipts.c.id == event_id)).first():
                return False
            conn.execute(
                insert(schema.workos_event_receipts),
                {"id": event_id, "event_type": event_type, "payload": payload, "created_at": utcnow()},
            )
        return True

    def mark_workos_event_processed(self, event_id: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(schema.workos_event_receipts)
                .where(schema.workos_event_receipts.c.id == event_id)
                .values(processed_at=utcnow())
            )

    def get_workos_event(self, event_id: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = conn.execute(
                select(schema.workos_event_receipts).where(schema.workos_event_receipts.c.id == event_id)
            ).mappings().first()
        return dict(row) if row else None

    def apply_workos_event(self, event_id: str) -> bool:
        """Mirror one durable WorkOS lifecycle event; defer if dependencies have not arrived yet."""
        receipt = self.get_workos_event(event_id)
        if not receipt:
            raise LookupError("workos_event_not_found")
        payload = dict(receipt.get("payload") or {})
        data = dict(payload.get("data") or {})
        event_type = str(receipt["event_type"])
        now = utcnow()
        source_updated_at = _workos_timestamp(payload, data)
        with self.engine.begin() as conn:
            if event_type.startswith("organization."):
                workos_id = str(data.get("id") or "")
                if not workos_id:
                    return False
                existing = conn.execute(
                    select(schema.organizations).where(schema.organizations.c.workos_organization_id == workos_id)
                ).mappings().first()
                values = {
                    "name": str(data.get("name") or workos_id),
                    "status": "inactive" if event_type.endswith(".deleted") else str(data.get("status") or "active"),
                    "workos_organization_id": workos_id,
                    "workos_updated_at": source_updated_at,
                }
                if _is_stale(dict(existing) if existing else None, source_updated_at):
                    values = {}
                if existing:
                    if values:
                        conn.execute(update(schema.organizations).where(schema.organizations.c.id == existing["id"]).values(**values))
                elif values:
                    conn.execute(
                        insert(schema.organizations),
                        {"id": new_id("org"), "slug": f"workos-{workos_id.lower()}", "created_at": now, **values},
                    )
            elif event_type.startswith("user."):
                workos_id = str(data.get("id") or "")
                email = str(data.get("email") or "")
                if not workos_id or not email:
                    return False
                is_deleted = event_type.endswith(".deleted")
                normalized_email = email.lower().strip()
                existing = conn.execute(select(schema.users).where(schema.users.c.workos_user_id == workos_id)).mappings().first()
                if not existing and not is_deleted:
                    existing = conn.execute(select(schema.users).where(schema.users.c.email == normalized_email)).mappings().first()
                name = " ".join(str(data.get(key) or "").strip() for key in ("first_name", "last_name")).strip() or email
                values = {
                    "email": normalized_email,
                    "name": name,
                    "status": "inactive" if is_deleted else str(data.get("status") or "active"),
                    "workos_user_id": workos_id,
                    "workos_updated_at": source_updated_at,
                }
                if _is_stale(dict(existing) if existing else None, source_updated_at):
                    values = {}
                if existing:
                    if values:
                        conn.execute(update(schema.users).where(schema.users.c.id == existing["id"]).values(**values))
                elif values and not is_deleted:
                    conn.execute(insert(schema.users), {"id": new_id("user"), "is_internal": False, "created_at": now, **values})
            elif event_type.startswith("organization_membership."):
                organization_id = conn.execute(
                    select(schema.organizations.c.id).where(
                        schema.organizations.c.workos_organization_id == str(data.get("organization_id") or "")
                    )
                ).scalar_one_or_none()
                user_id = conn.execute(
                    select(schema.users.c.id).where(schema.users.c.workos_user_id == str(data.get("user_id") or ""))
                ).scalar_one_or_none()
                if not organization_id or not user_id:
                    return False
                user_email = conn.execute(select(schema.users.c.email).where(schema.users.c.id == user_id)).scalar_one_or_none()
                workos_id = str(data.get("id") or "")
                role = data.get("role") or "viewer"
                if isinstance(role, dict):
                    role = role.get("slug") or "viewer"
                status = "inactive" if event_type.endswith((".deleted", ".deactivated")) else str(data.get("status") or "active")
                membership = conn.execute(
                    select(schema.organization_memberships).where(
                        and_(
                            schema.organization_memberships.c.organization_id == organization_id,
                            schema.organization_memberships.c.user_id == user_id,
                        )
                    )
                ).mappings().first()
                existing_role = str(membership["role"]) if membership else None
                values = {
                    "role": _local_role_from_workos(str(role), existing_role),
                    "status": status,
                    "workos_membership_id": workos_id or None,
                    "workos_updated_at": source_updated_at,
                }
                if _is_stale(dict(membership) if membership else None, source_updated_at):
                    values = {}
                if membership:
                    if values:
                        conn.execute(
                            update(schema.organization_memberships)
                            .where(
                                and_(
                                    schema.organization_memberships.c.organization_id == organization_id,
                                    schema.organization_memberships.c.user_id == user_id,
                                )
                            )
                            .values(**values)
                        )
                elif values:
                    conn.execute(
                        insert(schema.organization_memberships),
                        {"organization_id": organization_id, "user_id": user_id, "created_at": now, **values},
                    )
                if values and status == "active":
                    _accept_pending_invitations_for_email(conn, organization_id, str(user_email or ""), source_updated_at)
            elif event_type.startswith("invitation."):
                workos_id = str(data.get("id") or "")
                if not workos_id:
                    return False
                existing = conn.execute(
                    select(schema.organization_invitations).where(schema.organization_invitations.c.workos_invitation_id == workos_id)
                ).mappings().first()
                status = "revoked" if event_type.endswith((".deleted", ".revoked")) else str(data.get("state") or data.get("status") or "pending")
                raw_role = data.get("role_slug") or data.get("role")
                role = raw_role if raw_role is not None else "viewer"
                if isinstance(role, dict):
                    role = role.get("slug") or "viewer"
                existing_role = str(existing["role"]) if existing else None
                local_role = _local_role_from_workos(role, existing_role)
                values = {"status": status, "workos_updated_at": source_updated_at}
                if data.get("email"):
                    values["email"] = str(data["email"]).lower()
                if raw_role is not None:
                    values["role"] = local_role
                if existing:
                    email = str(values.get("email") or existing.get("email") or "")
                    if values["status"] == "pending" and _has_active_membership_for_email(conn, str(existing["organization_id"]), email):
                        values["status"] = "accepted"
                    if not _is_stale(dict(existing), source_updated_at):
                        conn.execute(
                            update(schema.organization_invitations)
                            .where(schema.organization_invitations.c.id == existing["id"])
                            .values(**values)
                        )
                else:
                    workos_organization_id = data.get("organization_id") or data.get("organization")
                    if isinstance(workos_organization_id, dict):
                        workos_organization_id = workos_organization_id.get("id")
                    organization_id = conn.execute(
                        select(schema.organizations.c.id).where(
                            schema.organizations.c.workos_organization_id == str(workos_organization_id or "")
                        )
                    ).scalar_one_or_none()
                    if not organization_id or not data.get("email"):
                        return False
                    if values["status"] == "pending" and _has_active_membership_for_email(conn, organization_id, str(data["email"])):
                        values["status"] = "accepted"
                    invited_by_user_id = conn.execute(
                        select(schema.users.c.id).where(schema.users.c.workos_user_id == str(data.get("inviter_user_id") or ""))
                    ).scalar_one_or_none()
                    conn.execute(
                        insert(schema.organization_invitations),
                        {
                            "id": new_id("invitation"),
                            "organization_id": organization_id,
                            "workos_invitation_id": workos_id,
                            "role": local_role,
                            "invited_by_user_id": invited_by_user_id,
                            "created_at": now,
                            **values,
                        },
                    )
            conn.execute(
                update(schema.workos_event_receipts)
                .where(schema.workos_event_receipts.c.id == event_id)
                .values(processed_at=now)
            )
        return True

    def list_pending_workos_event_ids(self) -> list[str]:
        with self.engine.begin() as conn:
            rows = conn.execute(
                select(schema.workos_event_receipts.c.id)
                .where(schema.workos_event_receipts.c.processed_at.is_(None))
                .order_by(schema.workos_event_receipts.c.created_at)
            )
            return [str(row[0]) for row in rows]

    def get_workos_sync_cursor(self) -> str | None:
        with self.engine.begin() as conn:
            return conn.execute(
                select(schema.workos_sync_cursors.c.cursor).where(schema.workos_sync_cursors.c.id == "workos-events")
            ).scalar_one_or_none()

    def set_workos_sync_cursor(self, cursor: str) -> None:
        with self.engine.begin() as conn:
            if conn.execute(select(schema.workos_sync_cursors.c.id).where(schema.workos_sync_cursors.c.id == "workos-events")).first():
                conn.execute(
                    update(schema.workos_sync_cursors)
                    .where(schema.workos_sync_cursors.c.id == "workos-events")
                    .values(cursor=cursor)
                )
            else:
                conn.execute(
                    insert(schema.workos_sync_cursors),
                    {"id": "workos-events", "cursor": cursor, "created_at": utcnow()},
                )

    def audit(self, actor_user_id: str, organization_id: str | None, action: str, target_type: str, target_id: str, metadata: dict[str, Any] | None = None) -> None:
        with self._tenant_transaction(organization_id, internal=organization_id is None) as conn:
            conn.execute(
                insert(schema.audit_events),
                {
                    "id": new_id("audit"),
                    "actor_user_id": actor_user_id,
                    "actor_type": "staff" if actor_user_id == DEV_STAFF_ID else "user",
                    "organization_id": organization_id,
                    "action": action,
                    "target_type": target_type,
                    "target_id": target_id,
                    "metadata": metadata or {},
                    "created_at": utcnow(),
                },
            )


@lru_cache
def get_product_store() -> ProductStore:
    settings = get_settings()
    store = ProductStore(create_engine(settings.product_database_url), settings)
    store.ensure_schema()
    return store
