"""commercial SaaS: organizations, users, memberships, sessions, saved searches, alerts,
usage counters; domain suppression

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
TS = sa.DateTime(timezone=True)


def _ts(name, nullable=False, default=True):
    return sa.Column(name, TS, server_default=sa.func.now() if default else None, nullable=nullable)


def upgrade() -> None:
    with op.batch_alter_table("domains") as b:
        b.add_column(sa.Column("suppressed_at", TS, nullable=True))
        b.add_column(sa.Column("suppression_reason", sa.String(300), nullable=True))
        b.create_index("ix_domains_suppressed_at", ["suppressed_at"])

    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("plan", sa.String(20), server_default="free", nullable=False),
        sa.Column("billing_customer_id", sa.String(100), nullable=True),
        sa.Column("subscription_status", sa.String(30), nullable=True),
        _ts("created_at"), _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
    )
    op.create_index("ix_organizations_billing_customer_id", "organizations", ["billing_customer_id"])
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid()),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(300), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("failed_logins", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_until", TS, nullable=True),
        sa.Column("last_login_at", TS, nullable=True),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid()),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_memberships"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_memberships_user_id_users"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_memberships_organization_id_organizations"),
        sa.UniqueConstraint("user_id", "organization_id", name="uq_memberships_user_id"),
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])
    op.create_index("ix_memberships_organization_id", "memberships", ["organization_id"])
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid()),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_auth_sessions_user_id_users"),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])
    op.create_table(
        "saved_searches",
        sa.Column("id", sa.Uuid()),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("filters", JSONType, nullable=False),
        sa.Column("alerts_enabled", sa.Boolean(), nullable=False),
        sa.Column("last_alert_check_at", TS, nullable=True),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_saved_searches"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_saved_searches_organization_id_organizations"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_saved_searches_user_id_users"),
    )
    op.create_index("ix_saved_searches_organization_id", "saved_searches", ["organization_id"])
    op.create_table(
        "alerts",
        sa.Column("id", sa.Uuid()),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("saved_search_id", sa.Uuid(), nullable=False),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        _ts("created_at"),
        sa.Column("read_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_alerts"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_alerts_organization_id_organizations"),
        sa.ForeignKeyConstraint(["saved_search_id"], ["saved_searches.id"], name="fk_alerts_saved_search_id_saved_searches"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], name="fk_alerts_store_id_stores"),
        sa.UniqueConstraint("saved_search_id", "store_id", name="uq_alerts_saved_search_id"),
    )
    op.create_index("ix_alerts_organization_id", "alerts", ["organization_id"])
    op.create_index("ix_alerts_created_at", "alerts", ["created_at"])
    op.create_table(
        "usage_counters",
        sa.Column("id", sa.Uuid()),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("metric", sa.String(50), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_usage_counters"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], name="fk_usage_counters_organization_id_organizations"),
        sa.UniqueConstraint("organization_id", "metric", "period", name="uq_usage_counters_organization_id"),
    )
    op.create_index("ix_usage_counters_organization_id", "usage_counters", ["organization_id"])


def downgrade() -> None:
    for t in ("usage_counters", "alerts", "saved_searches", "auth_sessions", "memberships", "users", "organizations"):
        op.drop_table(t)
    with op.batch_alter_table("domains") as b:
        b.drop_index("ix_domains_suppressed_at")
        b.drop_column("suppression_reason")
        b.drop_column("suppressed_at")
