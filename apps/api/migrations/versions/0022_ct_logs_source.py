"""M23: ct_logs discovery source (new TLS certificates → Shopify DNS filter). Seeded DISABLED;
the release enables it after a live test batch.

Revision ID: 0022
Revises: 0021
"""
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
CONFIG = {
    "logs": ["https://ct.cloudflare.com/logs/nimbus2026/", "https://ct.cloudflare.com/logs/nimbus2027/"],
    "match_cidrs": ["23.227.38.0/24"], "pages_per_batch": 4, "page_size": 1000, "dns_threads": 64,
    "interval_seconds": 2, "verify_new_limit": 2000, "backlog_rank": 1, "schedule_hours": 0.25,
}  # fmt: skip


def upgrade() -> None:
    conn = op.get_bind()
    src = sa.table("discovery_sources", sa.column("id", sa.Uuid()), sa.column("name", sa.String()),
                   sa.column("source_type", sa.String()), sa.column("configuration", JSONType), sa.column("state", JSONType),
                   sa.column("enabled", sa.Boolean()), sa.column("created_at", sa.DateTime(timezone=True)),
                   sa.column("updated_at", sa.DateTime(timezone=True)))  # fmt: skip
    if not conn.execute(sa.select(src.c.id).where(src.c.name == "ct_logs")).first():
        now = datetime.now(UTC)
        op.bulk_insert(src, [{"id": uuid.uuid4(), "name": "ct_logs", "source_type": "ct", "configuration": CONFIG,
                              "state": {}, "enabled": False, "created_at": now, "updated_at": now}])  # fmt: skip


def downgrade() -> None:
    op.execute("DELETE FROM discovery_sources WHERE name = 'ct_logs' AND NOT EXISTS "
               "(SELECT 1 FROM discovery_runs r WHERE r.source_id = discovery_sources.id)")
