"""products: status/sku/category/method, per-store URL uniqueness, product_observations

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("products") as b:
        b.add_column(sa.Column("sku", sa.String(200), nullable=True))
        b.add_column(sa.Column("category", sa.String(300), nullable=True))
        b.add_column(sa.Column("status", sa.String(20), server_default="active", nullable=False))
        b.add_column(sa.Column("extraction_method", sa.String(30), nullable=True))
        b.create_index("ix_products_status", ["status"])
        b.create_index("ix_products_last_seen_at", ["last_seen_at"])
        b.create_unique_constraint("uq_products_store_id", ["store_id", "product_url"])

    op.create_table(
        "product_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("change_type", sa.String(20), nullable=False),
        sa.Column("price", sa.Numeric(14, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("availability", sa.String(30), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("details", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name="fk_product_observations_product_id_products"),
        sa.PrimaryKeyConstraint("id", name="pk_product_observations"),
    )
    op.create_index("ix_product_observations_product_observed", "product_observations", ["product_id", "observed_at"])
    op.create_index("ix_product_observations_change_type", "product_observations", ["change_type"])


def downgrade() -> None:
    op.drop_index("ix_product_observations_change_type", table_name="product_observations")
    op.drop_index("ix_product_observations_product_observed", table_name="product_observations")
    op.drop_table("product_observations")
    with op.batch_alter_table("products") as b:
        b.drop_constraint("uq_products_store_id", type_="unique")
        b.drop_index("ix_products_last_seen_at")
        b.drop_index("ix_products_status")
        b.drop_column("extraction_method")
        b.drop_column("status")
        b.drop_column("category")
        b.drop_column("sku")
