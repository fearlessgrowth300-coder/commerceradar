"""suppression audit trail

Revision ID: 0010
Revises: 0009
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "suppression_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("subdomains", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_suppression_events"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], name="fk_suppression_events_actor_user_id_users"),
    )
    op.create_index("ix_suppression_events_domain", "suppression_events", ["domain"])
    op.create_index("ix_suppression_events_created_at", "suppression_events", ["created_at"])
    # Backfill: existing opt-outs get an initial event so the trail is complete.
    op.execute(
        "INSERT INTO suppression_events (id, domain, action, reason, subdomains, created_at) "
        "SELECT id, normalized_domain, 'suppress', suppression_reason, 0, suppressed_at FROM domains WHERE suppressed_at IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_suppression_events_created_at", table_name="suppression_events")
    op.drop_index("ix_suppression_events_domain", table_name="suppression_events")
    op.drop_table("suppression_events")
