"""backlog_rank: verify candidates from high-yield sources first

Measured 2026-11-04 (stores / processed candidates): urlscan 64 % (16/25), Common Crawl 5.4 %
(69/1283), crt.sh 9.8 % but 16 of 18 already known (circular seeds). 441 urlscan candidates
were waiting behind 7,217 Common Crawl ones because the backlog was worked oldest-first.

Revision ID: 0011
Revises: 0010
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
RANKS = {"urlscan": 1, "common_crawl": 3, "commerceradar_crawler": 3, "certificate_transparency": 5}
PENDING = "verification_status = 'pending'"


def upgrade() -> None:
    with op.batch_alter_table("domains") as b:
        b.add_column(sa.Column("backlog_rank", sa.Integer(), server_default="5", nullable=False))
    op.drop_index("ix_domains_pending_first_seen", table_name="domains")
    op.create_index("ix_domains_pending_rank_first_seen", "domains", ["backlog_rank", "first_seen_at"],
                    postgresql_where=sa.text(PENDING), sqlite_where=sa.text(PENDING))

    conn = op.get_bind()
    src = sa.table("discovery_sources", sa.column("id", sa.Uuid()), sa.column("name", sa.String()), sa.column("configuration", JSONType))
    for sid, name, cfg in conn.execute(sa.select(src.c.id, src.c.name, src.c.configuration)).all():
        if name in RANKS:
            conn.execute(src.update().where(src.c.id == sid).values(configuration={**(cfg or {}), "backlog_rank": RANKS[name]}))
            # existing candidates take the best rank of any source that found them
            conn.execute(sa.text(
                "UPDATE domains SET backlog_rank = :r WHERE backlog_rank > :r AND id IN "
                "(SELECT domain_id FROM discovered_urls WHERE source_id = :sid)"
            ), {"r": RANKS[name], "sid": sid})


def downgrade() -> None:
    op.drop_index("ix_domains_pending_rank_first_seen", table_name="domains")
    op.create_index("ix_domains_pending_first_seen", "domains", ["first_seen_at"],
                    postgresql_where=sa.text(PENDING), sqlite_where=sa.text(PENDING))
    with op.batch_alter_table("domains") as b:
        b.drop_column("backlog_rank")
