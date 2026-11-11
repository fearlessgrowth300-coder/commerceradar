"""stores already downloaded per organization (skip them in later downloads)

Revision ID: 0020
Revises: 0019
"""
import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exported_stores",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        sa.Column("first_exported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_exported_stores_organization_id_organizations"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], name="fk_exported_stores_store_id_stores"),
        sa.PrimaryKeyConstraint("id", name="pk_exported_stores"),
        sa.UniqueConstraint("organization_id", "store_id", name="uq_exported_stores_organization_id"),
    )
    op.create_index("ix_exported_stores_store_id", "exported_stores", ["store_id"])


def downgrade() -> None:
    op.drop_index("ix_exported_stores_store_id", table_name="exported_stores")
    op.drop_table("exported_stores")
