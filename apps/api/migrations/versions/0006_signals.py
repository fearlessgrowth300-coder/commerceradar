"""intelligence signals: store_signals + stores.signals_refreshed_at

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("stores") as b:
        b.add_column(sa.Column("signals_refreshed_at", sa.DateTime(timezone=True), nullable=True))
        b.create_index("ix_stores_signals_refreshed_at", ["signals_refreshed_at"])
    op.create_table(
        "store_signals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        sa.Column("signal_type", sa.String(50), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("basis", sa.String(10), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_detected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], name="fk_store_signals_store_id_stores"),
        sa.PrimaryKeyConstraint("id", name="pk_store_signals"),
        sa.UniqueConstraint("store_id", "signal_type", name="uq_store_signals_store_id"),
    )
    op.create_index("ix_store_signals_type_active", "store_signals", ["signal_type", "active"])
    op.create_index("ix_store_signals_category", "store_signals", ["category"])
    op.create_index("ix_store_signals_last_detected_at", "store_signals", ["last_detected_at"])


def downgrade() -> None:
    op.drop_index("ix_store_signals_last_detected_at", table_name="store_signals")
    op.drop_index("ix_store_signals_category", table_name="store_signals")
    op.drop_index("ix_store_signals_type_active", table_name="store_signals")
    op.drop_table("store_signals")
    with op.batch_alter_table("stores") as b:
        b.drop_index("ix_stores_signals_refreshed_at")
        b.drop_column("signals_refreshed_at")
