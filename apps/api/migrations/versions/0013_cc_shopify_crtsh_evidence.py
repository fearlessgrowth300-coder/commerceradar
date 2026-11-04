"""M10 phases 3-4: discovered_urls.source_ref, Common Crawl myshopify.com source,
crt.sh becomes evidence-only, certificate signal renamed (no launch-date inference)

Revision ID: 0013
Revises: 0012
"""
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
CC_SHOPIFY = {
    "index": "CC-MAIN-2026-43", "host_suffixes": ["myshopify.com"], "blocks_per_batch": 3,
    "max_blocks_per_run": 120,  # the whole suffix is ~95 index blocks (measured)
    "interval_seconds": 5, "verify_new_limit": 25, "backlog_rank": 2, "schedule_hours": 168,
}  # fmt: skip


def upgrade() -> None:
    with op.batch_alter_table("discovered_urls") as b:
        b.add_column(sa.Column("source_ref", sa.String(200), nullable=True))

    conn = op.get_bind()
    src = sa.table("discovery_sources", sa.column("id", sa.Uuid()), sa.column("name", sa.String()),
                   sa.column("source_type", sa.String()), sa.column("configuration", JSONType), sa.column("state", JSONType),
                   sa.column("enabled", sa.Boolean()), sa.column("created_at", sa.DateTime(timezone=True)),
                   sa.column("updated_at", sa.DateTime(timezone=True)))  # fmt: skip
    if not conn.execute(sa.select(src.c.id).where(src.c.name == "common_crawl_shopify")).first():
        now = datetime.now(UTC)
        op.bulk_insert(src, [{"id": uuid.uuid4(), "name": "common_crawl_shopify", "source_type": "commoncrawl",
                              "configuration": CC_SHOPIFY, "state": {}, "enabled": True, "created_at": now, "updated_at": now}])  # fmt: skip

    for sid, cfg in conn.execute(sa.select(src.c.id, src.c.configuration).where(src.c.source_type == "crtsh")).all():
        cfg = {k: v for k, v in (cfg or {}).items() if k != "schedule_hours"}  # no scheduled discovery runs
        cfg.update({"evidence_only": True, "verify_new_limit": 0, "backlog_rank": 9})
        conn.execute(src.update().where(src.c.id == sid).values(configuration=cfg))

    op.execute("UPDATE store_signals SET signal_type = 'recent_first_certificate', category = 'change', basis = 'observed' "
               "WHERE signal_type = 'possibly_recently_launched'")


def downgrade() -> None:
    op.execute("UPDATE store_signals SET signal_type = 'possibly_recently_launched', category = 'growth', basis = 'inferred' "
               "WHERE signal_type = 'recent_first_certificate'")
    op.execute("DELETE FROM discovery_sources WHERE name = 'common_crawl_shopify' AND NOT EXISTS "
               "(SELECT 1 FROM discovery_runs r WHERE r.source_id = discovery_sources.id)")
    with op.batch_alter_table("discovered_urls") as b:
        b.drop_column("source_ref")
