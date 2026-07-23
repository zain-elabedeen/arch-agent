"""Allow WorkOS reconciliation to mirror externally created invitations."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_external_workos_invitations"
down_revision = "0006_workos_source_timestamps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("organization_invitations") as batch_op:
        batch_op.alter_column("invited_by_user_id", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM organization_invitations WHERE invited_by_user_id IS NULL")
    with op.batch_alter_table("organization_invitations") as batch_op:
        batch_op.alter_column("invited_by_user_id", existing_type=sa.String(), nullable=False)
