"""discovery: run cursor, source state, URL dedupe per source, default sources

Revision ID: 0003
Revises: 0002
"""
import uuid
from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")

# Free sources only (spec §7). Disabled flags / limits are editable later via the sources table.
DEFAULT_SOURCES = [
    ("common_crawl", "commoncrawl", {
        "index": "CC-MAIN-2026-43", "host_suffixes": ["shop", "store", "boutique"],
        "blocks_per_batch": 3, "max_blocks_per_run": 60, "interval_seconds": 5, "verify_new_limit": 200,
    }),
    ("certificate_transparency", "crtsh", {
        "queries": [], "seed_from_stores": True, "max_store_queries": 200,
        "interval_seconds": 15, "verify_new_limit": 100,
    }),
    ("commerceradar_crawler", "crawler", {
        "max_pages_per_site": 5, "page_delay_seconds": 1.0, "max_sites_per_run": 50,
        "recrawl_days": 30, "interval_seconds": 2, "verify_new_limit": 100,
    }),
    ("urlscan", "urlscan", {
        "query": "domain:myshopify.com", "page_size": 100, "max_pages_per_run": 5,
        "interval_seconds": 6, "verify_new_limit": 200,
    }),
]  # fmt: skip


def upgrade() -> None:
    with op.batch_alter_table("discovery_sources") as b:
        b.add_column(sa.Column("state", JSONType, nullable=True))
    with op.batch_alter_table("discovery_runs") as b:
        b.add_column(sa.Column("cursor", JSONType, nullable=True))
    with op.batch_alter_table("discovered_urls") as b:
        b.add_column(sa.Column("domain_id", sa.Uuid(), nullable=True))
        b.create_index("ix_discovered_urls_domain_id", ["domain_id"])
        b.create_foreign_key("fk_discovered_urls_domain_id_domains", "domains", ["domain_id"], ["id"])
        b.create_unique_constraint("uq_discovered_urls_source_id", ["source_id", "normalized_url"])

    sources = sa.table(
        "discovery_sources",
        sa.column("id", sa.Uuid()), sa.column("name", sa.String()), sa.column("source_type", sa.String()),
        sa.column("configuration", JSONType), sa.column("state", JSONType), sa.column("enabled", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)), sa.column("updated_at", sa.DateTime(timezone=True)),
    )  # fmt: skip
    now = datetime.now(UTC)
    op.bulk_insert(
        sources,
        [
            {"id": uuid.uuid4(), "name": n, "source_type": t, "configuration": c, "state": {}, "enabled": True,
             "created_at": now, "updated_at": now}
            for n, t, c in DEFAULT_SOURCES
        ],  # fmt: skip
    )


def downgrade() -> None:
    op.execute("DELETE FROM discovered_urls")
    op.execute("DELETE FROM discovery_runs")
    op.execute("DELETE FROM discovery_sources")
    with op.batch_alter_table("discovered_urls") as b:
        b.drop_constraint("uq_discovered_urls_source_id", type_="unique")
        b.drop_constraint("fk_discovered_urls_domain_id_domains", type_="foreignkey")
        b.drop_index("ix_discovered_urls_domain_id")
        b.drop_column("domain_id")
    with op.batch_alter_table("discovery_runs") as b:
        b.drop_column("cursor")
    with op.batch_alter_table("discovery_sources") as b:
        b.drop_column("state")
