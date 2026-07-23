"""Create the tenant-aware product control-plane schema."""

from __future__ import annotations

from alembic import op

from agent.app.product.schema import metadata

revision = "0001_product_control_plane"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    metadata.drop_all(bind=op.get_bind())
