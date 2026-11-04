"""reset crt.sh incremental state: earlier runs fetched only unexpired certificates, so their
"earliest certificate" dates were too recent. The next run re-reads full history and the
ingest upsert lowers first_discovered_at to the true earliest sighting.

Revision ID: 0007
Revises: 0006
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE discovery_sources SET state = NULL WHERE source_type = 'crtsh'")
    # Signals derived from the old, wrong dates are withdrawn until recomputed from full history.
    op.execute("UPDATE store_signals SET active = false, resolved_at = CURRENT_TIMESTAMP "
               "WHERE signal_type = 'possibly_recently_launched' AND active = true")


def downgrade() -> None:
    pass  # data correction; nothing to undo
