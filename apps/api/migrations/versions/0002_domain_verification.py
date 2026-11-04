"""domain verification status + job result

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("domains") as b:
        b.add_column(sa.Column("verification_status", sa.String(30), server_default="pending", nullable=False))
        b.add_column(sa.Column("verification_confidence", sa.Float(), nullable=True))
        b.add_column(sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True))
        b.create_index("ix_domains_verification_status", ["verification_status"])
        b.create_index("ix_domains_last_checked_at", ["last_checked_at"])

    with op.batch_alter_table("crawl_jobs") as b:
        b.add_column(sa.Column("domain_id", sa.Uuid(), nullable=True))
        b.add_column(
            sa.Column("result", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=True)
        )
        b.create_index("ix_crawl_jobs_domain_id", ["domain_id"])
        b.create_foreign_key("fk_crawl_jobs_domain_id_domains", "domains", ["domain_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("crawl_jobs") as b:
        b.drop_constraint("fk_crawl_jobs_domain_id_domains", type_="foreignkey")
        b.drop_index("ix_crawl_jobs_domain_id")
        b.drop_column("result")
        b.drop_column("domain_id")

    with op.batch_alter_table("domains") as b:
        b.drop_index("ix_domains_last_checked_at")
        b.drop_index("ix_domains_verification_status")
        b.drop_column("last_checked_at")
        b.drop_column("verification_confidence")
        b.drop_column("verification_status")
