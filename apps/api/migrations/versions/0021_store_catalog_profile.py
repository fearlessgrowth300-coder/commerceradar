"""store catalog profile: language, product dates, formats, category (all nullable, additive)

Revision ID: 0021
Revises: 0020
"""
import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

COLUMNS = [
    ("language", sa.String(8)),
    ("published_products", sa.Integer()),
    ("first_product_at", sa.DateTime(timezone=True)),
    ("latest_product_at", sa.DateTime(timezone=True)),
    ("category_group", sa.String(60)),
    ("category", sa.String(60)),
    ("category_confidence", sa.Float()),
    ("is_pod", sa.Boolean()),
    ("is_cod", sa.Boolean()),
    ("is_dropshipping", sa.Boolean()),
    ("is_digital", sa.Boolean()),
    ("catalog_checked_at", sa.DateTime(timezone=True)),
]
INDEXED = ["language", "first_product_at", "latest_product_at", "category_group", "category", "catalog_checked_at"]


def upgrade() -> None:
    for name, type_ in COLUMNS:
        op.add_column("stores", sa.Column(name, type_, nullable=True))
    for name in INDEXED:
        op.create_index(f"ix_stores_{name}", "stores", [name])


def downgrade() -> None:
    for name in INDEXED:
        op.drop_index(f"ix_stores_{name}", table_name="stores")
    for name, _ in reversed(COLUMNS):
        op.drop_column("stores", name)
