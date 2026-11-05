"""daily metric snapshots

Revision ID: 0017
Revises: 0016
"""
import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "metric_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("taken_on", sa.String(10), nullable=False),
        sa.Column("metric", sa.String(60), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_metric_snapshots"),
        sa.UniqueConstraint("taken_on", "metric", "source", name="uq_metric_snapshots_taken_on"),
    )
    op.create_index("ix_metric_snapshots_taken_on", "metric_snapshots", ["taken_on"])


def downgrade() -> None:
    op.drop_index("ix_metric_snapshots_taken_on", table_name="metric_snapshots")
    op.drop_table("metric_snapshots")
