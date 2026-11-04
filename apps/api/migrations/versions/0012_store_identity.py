"""store identity: store_domains links, storefront_key, merged_into_store_id

Backfill: every existing store gets a current PRIMARY link to its domain (alias_type "self").
No existing stores are merged by the migration; duplicates are folded only when a later
verification supplies strong evidence (see app/services/identity.py).

Revision ID: 0012
Revises: 0011
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
CUR_DOMAIN = "valid_to IS NULL AND role IN ('primary','alias')"
CUR_PRIMARY = "valid_to IS NULL AND role = 'primary'"


def upgrade() -> None:
    with op.batch_alter_table("stores") as b:
        b.add_column(sa.Column("storefront_key", sa.String(200), nullable=True))
        b.add_column(sa.Column("merged_into_store_id", sa.Uuid(), nullable=True))
        b.create_unique_constraint("uq_stores_storefront_key", ["storefront_key"])
        b.create_index("ix_stores_merged_into_store_id", ["merged_into_store_id"])
        b.create_foreign_key("fk_stores_merged_into_store_id_stores", "stores", ["merged_into_store_id"], ["id"])
    op.create_table(
        "store_domains",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("store_id", sa.Uuid(), nullable=False),
        sa.Column("domain_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("alias_type", sa.String(30), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence", JSONType, nullable=True),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_store_domains"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], name="fk_store_domains_store_id_stores"),
        sa.ForeignKeyConstraint(["domain_id"], ["domains.id"], name="fk_store_domains_domain_id_domains"),
    )
    op.create_index("ix_store_domains_store_id", "store_domains", ["store_id"])
    op.create_index("ix_store_domains_domain_id", "store_domains", ["domain_id"])
    op.create_index("uq_store_domains_current_domain", "store_domains", ["domain_id"], unique=True,
                    postgresql_where=sa.text(CUR_DOMAIN), sqlite_where=sa.text(CUR_DOMAIN))
    op.create_index("uq_store_domains_current_primary", "store_domains", ["store_id"], unique=True,
                    postgresql_where=sa.text(CUR_PRIMARY), sqlite_where=sa.text(CUR_PRIMARY))
    conn = op.get_bind()
    stores = conn.execute(sa.text("SELECT id, domain_id, coalesce(first_verified_at, created_at) FROM stores")).all()
    import uuid as _uuid

    ts_type = sa.DateTime(timezone=True)
    t = sa.table(
        "store_domains",
        sa.column("id", sa.Uuid()), sa.column("store_id", sa.Uuid()), sa.column("domain_id", sa.Uuid()),
        sa.column("role", sa.String()), sa.column("alias_type", sa.String()), sa.column("confidence", sa.Float()),
        sa.column("evidence", JSONType), sa.column("first_observed_at", ts_type), sa.column("last_observed_at", ts_type),
        sa.column("valid_from", ts_type),
    )  # fmt: skip
    if stores:
        op.bulk_insert(t, [{"id": _uuid.uuid4(), "store_id": sid, "domain_id": did, "role": "primary", "alias_type": "self",
                            "confidence": 1.0, "evidence": {"backfill": True}, "first_observed_at": ts,
                            "last_observed_at": ts, "valid_from": ts}
                           for sid, did, ts in ((_uuid.UUID(str(a)), _uuid.UUID(str(b)), c) for a, b, c in stores)])  # fmt: skip


def downgrade() -> None:
    op.drop_index("uq_store_domains_current_primary", table_name="store_domains")
    op.drop_index("uq_store_domains_current_domain", table_name="store_domains")
    op.drop_index("ix_store_domains_domain_id", table_name="store_domains")
    op.drop_index("ix_store_domains_store_id", table_name="store_domains")
    op.drop_table("store_domains")
    with op.batch_alter_table("stores") as b:
        b.drop_constraint("fk_stores_merged_into_store_id_stores", type_="foreignkey")
        b.drop_index("ix_stores_merged_into_store_id")
        b.drop_constraint("uq_stores_storefront_key", type_="unique")
        b.drop_column("merged_into_store_id")
        b.drop_column("storefront_key")
