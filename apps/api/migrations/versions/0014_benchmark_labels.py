"""benchmark labels (accuracy ground truth)

Revision ID: 0014
Revises: 0013
"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "benchmark_labels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("is_ecommerce", sa.Boolean(), nullable=False),
        sa.Column("platform", sa.String(30), nullable=True),
        sa.Column("label_source", sa.String(30), nullable=False),
        sa.Column("labeller", sa.String(100), nullable=True),
        sa.Column("split", sa.String(10), nullable=False),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_benchmark_labels"),
        sa.UniqueConstraint("domain", name="uq_benchmark_labels_domain"),
    )
    op.create_index("ix_benchmark_labels_label_source", "benchmark_labels", ["label_source"])
    op.create_index("ix_benchmark_labels_split", "benchmark_labels", ["split"])


def downgrade() -> None:
    op.drop_index("ix_benchmark_labels_split", table_name="benchmark_labels")
    op.drop_index("ix_benchmark_labels_label_source", table_name="benchmark_labels")
    op.drop_table("benchmark_labels")
