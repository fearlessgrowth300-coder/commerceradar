"""M13: czds_zonefile discovery source (new registrations → Shopify DNS filter). Seeded DISABLED:
it needs ICANN CZDS approval + CZDS_USERNAME/CZDS_PASSWORD and a release decision.

Revision ID: 0018
Revises: 0017
"""
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
CONFIG = {
    "tlds": ["shop", "store"], "match_cidrs": ["23.227.38.0/24"], "dns_per_batch": 2000, "dns_threads": 32,
    "interval_seconds": 2, "verify_new_limit": 200, "backlog_rank": 1, "schedule_hours": 24, "min_free_gb": 5,
}  # fmt: skip


def upgrade() -> None:
    conn = op.get_bind()
    src = sa.table("discovery_sources", sa.column("id", sa.Uuid()), sa.column("name", sa.String()),
                   sa.column("source_type", sa.String()), sa.column("configuration", JSONType), sa.column("state", JSONType),
                   sa.column("enabled", sa.Boolean()), sa.column("created_at", sa.DateTime(timezone=True)),
                   sa.column("updated_at", sa.DateTime(timezone=True)))  # fmt: skip
    if not conn.execute(sa.select(src.c.id).where(src.c.name == "czds_zonefile")).first():
        now = datetime.now(UTC)
        op.bulk_insert(src, [{"id": uuid.uuid4(), "name": "czds_zonefile", "source_type": "czds", "configuration": CONFIG,
                              "state": {}, "enabled": False, "created_at": now, "updated_at": now}])  # fmt: skip


def downgrade() -> None:
    op.execute("DELETE FROM discovery_sources WHERE name = 'czds_zonefile' AND NOT EXISTS "
               "(SELECT 1 FROM discovery_runs r WHERE r.source_id = discovery_sources.id)")
