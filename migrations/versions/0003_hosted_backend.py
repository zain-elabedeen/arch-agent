"""Add hosted authentication, collector, and snapshot state."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_hosted_backend"
down_revision = "0002_add_updated_at"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _add_column(table_name: str, column: sa.Column) -> bool:
    if column.name in _columns(table_name):
        return False
    op.add_column(table_name, column)
    return True


def _create_unique(name: str, table_name: str, columns: list[str]) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_unique_constraints(table_name)}
    if name not in existing:
        op.create_unique_constraint(name, table_name, columns)


def upgrade() -> None:
    _add_column("organizations", sa.Column("workos_organization_id", sa.String(), nullable=True))
    _create_unique("uq_organizations_workos_organization_id", "organizations", ["workos_organization_id"])
    _add_column("users", sa.Column("workos_user_id", sa.String(), nullable=True))
    _create_unique("uq_users_workos_user_id", "users", ["workos_user_id"])
    _add_column("organization_memberships", sa.Column("status", sa.String(), nullable=False, server_default="active"))
    _add_column("organization_memberships", sa.Column("workos_membership_id", sa.String(), nullable=True))
    _create_unique("uq_memberships_workos_membership_id", "organization_memberships", ["workos_membership_id"])
    _add_column("organization_invitations", sa.Column("workos_invitation_id", sa.String(), nullable=True))
    _create_unique("uq_invitations_workos_invitation_id", "organization_invitations", ["workos_invitation_id"])
    _add_column("collector_credentials", sa.Column("purpose", sa.String(), nullable=False, server_default="collector"))
    _add_column("collector_credentials", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    _add_column("collector_credentials", sa.Column("used_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE collector_credentials SET expires_at = CURRENT_TIMESTAMP WHERE expires_at IS NULL")
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column("collector_credentials", "expires_at", nullable=False)
    _add_column("snapshot_runs", sa.Column("snapshot", sa.JSON(), nullable=False, server_default="{}"))
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("workos_event_receipts"):
        op.create_table(
            "workos_event_receipts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("event_type", sa.String(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("processed_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    if not inspector.has_table("workos_sync_cursors"):
        op.create_table(
            "workos_sync_cursors",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("cursor", sa.String()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table("workos_sync_cursors")
    op.drop_table("workos_event_receipts")
    op.drop_column("snapshot_runs", "snapshot")
    op.drop_column("collector_credentials", "used_at")
    op.drop_column("collector_credentials", "expires_at")
    op.drop_column("collector_credentials", "purpose")
    op.drop_constraint("uq_invitations_workos_invitation_id", "organization_invitations", type_="unique")
    op.drop_column("organization_invitations", "workos_invitation_id")
    op.drop_constraint("uq_memberships_workos_membership_id", "organization_memberships", type_="unique")
    op.drop_column("organization_memberships", "workos_membership_id")
    op.drop_column("organization_memberships", "status")
    op.drop_constraint("uq_users_workos_user_id", "users", type_="unique")
    op.drop_column("users", "workos_user_id")
    op.drop_constraint("uq_organizations_workos_organization_id", "organizations", type_="unique")
    op.drop_column("organizations", "workos_organization_id")
