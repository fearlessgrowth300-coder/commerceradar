"""scale: partial index for the pending backlog pick, composite index for in-flight job checks

Measured 2026-11-04 (9.2k domains): the backlog pick hash-anti-joins every pending domain
then top-N sorts (11 ms now, linear in backlog). The partial index lets it walk pending
domains oldest-first and stop after N.

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_domains_pending_first_seen", "domains", ["first_seen_at"],
        postgresql_where=sa.text("verification_status = 'pending'"), sqlite_where=sa.text("verification_status = 'pending'"),
    )
    op.create_index("ix_crawl_jobs_domain_type_status", "crawl_jobs", ["domain_id", "job_type", "status"])


def downgrade() -> None:
    op.drop_index("ix_crawl_jobs_domain_type_status", table_name="crawl_jobs")
    op.drop_index("ix_domains_pending_first_seen", table_name="domains")
