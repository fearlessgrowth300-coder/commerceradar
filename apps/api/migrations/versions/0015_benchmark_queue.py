"""blind labelling queue

Revision ID: 0015
Revises: 0014
"""
import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "benchmark_queue",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("stratum", sa.String(40), nullable=False),
        sa.Column("batch", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_benchmark_queue"),
        sa.UniqueConstraint("domain", name="uq_benchmark_queue_domain"),
    )
    for col in ("stratum", "batch", "status"):
        op.create_index(f"ix_benchmark_queue_{col}", "benchmark_queue", [col])


def downgrade() -> None:
    for col in ("status", "batch", "stratum"):
        op.drop_index(f"ix_benchmark_queue_{col}", table_name="benchmark_queue")
    op.drop_table("benchmark_queue")
