"""store business location (self-reported, Shopify /meta.json)

Revision ID: 0019
Revises: 0018
"""
import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stores", sa.Column("business_country", sa.String(2), nullable=True))
    op.add_column("stores", sa.Column("business_region", sa.String(100), nullable=True))
    op.add_column("stores", sa.Column("business_city", sa.String(100), nullable=True))
    op.add_column("stores", sa.Column("location_source", sa.String(30), nullable=True))
    op.add_column("stores", sa.Column("location_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_stores_business_country", "stores", ["business_country"])


def downgrade() -> None:
    op.drop_index("ix_stores_business_country", table_name="stores")
    for c in ("location_checked_at", "location_source", "business_city", "business_region", "business_country"):
        op.drop_column("stores", c)
