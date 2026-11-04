"""monitoring: store activity evidence/confidence, check timestamps, scheduling; source schedules

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
# Default cadence for the seeded sources (hours). Remove the key to stop scheduling a source.
SCHEDULES = {"urlscan": 24, "commerceradar_crawler": 24, "common_crawl": 24, "certificate_transparency": 168}


def upgrade() -> None:
    with op.batch_alter_table("stores") as b:
        b.add_column(sa.Column("activity_confidence", sa.Float(), nullable=True))
        b.add_column(sa.Column("activity_evidence", JSONType, nullable=True))
        b.add_column(sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False))
        b.add_column(sa.Column("last_successful_check_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("last_failed_check_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=True))
        b.add_column(sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=True))
        b.create_index("ix_stores_next_check_at", ["next_check_at"])
    # Existing stores were last verified successfully at last_verified_at; next_check_at NULL ⇒ due now.
    op.execute("UPDATE stores SET last_successful_check_at = last_verified_at WHERE ecommerce_status IN ('confirmed_ecommerce','probable_ecommerce')")

    conn = op.get_bind()
    sources = sa.table("discovery_sources", sa.column("id", sa.Uuid()), sa.column("name", sa.String()), sa.column("configuration", JSONType))
    for sid, name, cfg in conn.execute(sa.select(sources.c.id, sources.c.name, sources.c.configuration)).all():
        if name in SCHEDULES:
            conn.execute(sources.update().where(sources.c.id == sid).values(configuration={**(cfg or {}), "schedule_hours": SCHEDULES[name]}))


def downgrade() -> None:
    with op.batch_alter_table("stores") as b:
        b.drop_index("ix_stores_next_check_at")
        for col in ("last_changed_at", "next_check_at", "last_failed_check_at", "last_successful_check_at",
                    "consecutive_failures", "activity_evidence", "activity_confidence"):  # fmt: skip
            b.drop_column(col)
