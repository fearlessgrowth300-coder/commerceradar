"""restricted sources (urlscan quarantine), richer benchmark labels, holdout evaluation log

Revision ID: 0016
Revises: 0015
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

URLSCAN_REASON = ("urlscan terms (read 2026-11-04): commercial use and compiling a database from the service require "
                  "express written permission - not obtained. Records kept for provenance; quarantined.")


def upgrade() -> None:
    with op.batch_alter_table("discovery_sources") as b:
        b.add_column(sa.Column("restricted", sa.Boolean(), server_default=sa.false(), nullable=False))
        b.add_column(sa.Column("restriction_reason", sa.String(300), nullable=True))
    src = sa.table("discovery_sources", sa.column("source_type", sa.String()), sa.column("restricted", sa.Boolean()),
                   sa.column("restriction_reason", sa.String()), sa.column("enabled", sa.Boolean()))
    op.execute(src.update().where(src.c.source_type == "urlscan").values(restricted=True, enabled=False, restriction_reason=URLSCAN_REASON))

    with op.batch_alter_table("benchmark_labels") as b:
        b.alter_column("is_ecommerce", existing_type=sa.Boolean(), nullable=True)
        b.add_column(sa.Column("label", sa.String(30), nullable=True))
        b.add_column(sa.Column("labelled_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE benchmark_labels SET label = CASE WHEN is_ecommerce THEN 'store' ELSE 'not_store' END, labelled_at = created_at")

    op.create_table(
        "benchmark_evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("split", sa.String(10), nullable=False),
        sa.Column("requested_by", sa.String(320), nullable=True),
        sa.Column("labels", sa.Integer(), nullable=False),
        sa.Column("summary", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_benchmark_evaluations"),
    )
    op.create_index("ix_benchmark_evaluations_split", "benchmark_evaluations", ["split"])
    op.create_index("ix_benchmark_evaluations_created_at", "benchmark_evaluations", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_benchmark_evaluations_created_at", table_name="benchmark_evaluations")
    op.drop_index("ix_benchmark_evaluations_split", table_name="benchmark_evaluations")
    op.drop_table("benchmark_evaluations")
    with op.batch_alter_table("benchmark_labels") as b:
        b.drop_column("labelled_at")
        b.drop_column("label")
    with op.batch_alter_table("discovery_sources") as b:
        b.drop_column("restriction_reason")
        b.drop_column("restricted")
