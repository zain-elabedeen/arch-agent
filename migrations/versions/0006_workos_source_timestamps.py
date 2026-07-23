"""Track WorkOS lifecycle status and source timestamps."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_workos_source_timestamps"
down_revision = "0005_tenant_row_level_security"
branch_labels = None
depends_on = None


def _add_column(table_name: str, column: sa.Column) -> None:
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)


def upgrade() -> None:
    _add_column("organizations", sa.Column("status", sa.String(), nullable=False, server_default="active"))
    _add_column("organizations", sa.Column("workos_updated_at", sa.DateTime(timezone=True)))
    _add_column("users", sa.Column("status", sa.String(), nullable=False, server_default="active"))
    _add_column("users", sa.Column("workos_updated_at", sa.DateTime(timezone=True)))
    _add_column("organization_memberships", sa.Column("workos_updated_at", sa.DateTime(timezone=True)))
    _add_column("organization_invitations", sa.Column("workos_updated_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("organization_invitations", "workos_updated_at")
    op.drop_column("organization_memberships", "workos_updated_at")
    op.drop_column("users", "workos_updated_at")
    op.drop_column("users", "status")
    op.drop_column("organizations", "workos_updated_at")
    op.drop_column("organizations", "status")
