"""All database models. Split into a package once this file gets unwieldy.

Status-like columns are plain strings (validated in services), not native PG
enums, so adding a value never needs an ALTER TYPE migration.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _ts(index: bool = False) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), index=index)


def _updated() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


def _opt_ts(index: bool = False) -> Mapped[datetime | None]:
    return mapped_column(DateTime(timezone=True), index=index)


class DiscoverySource(Base):
    __tablename__ = "discovery_sources"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(100), unique=True)
    source_type: Mapped[str] = mapped_column(String(50))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Incremental progress that outlives a single run (e.g. highest crt.sh id seen per query).
    state: Mapped[dict[str, Any] | None] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _updated()


class DiscoveryRun(Base):
    __tablename__ = "discovery_runs"
    id: Mapped[uuid.UUID] = _pk()
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("discovery_sources.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    started_at: Mapped[datetime | None] = _opt_ts(index=True)
    completed_at: Mapped[datetime | None] = _opt_ts()
    records_found: Mapped[int] = mapped_column(Integer, default=0)
    records_processed: Mapped[int] = mapped_column(Integer, default=0)
    records_failed: Mapped[int] = mapped_column(Integer, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text)
    # Where the next batch starts; lets an interrupted run resume. None once finished.
    cursor: Mapped[dict[str, Any] | None] = mapped_column(JSONType)


class DiscoveredUrl(Base):
    """One row per (source, URL): the same URL seen by two sources keeps both provenances,
    while the domain itself is deduplicated in `domains`."""

    __tablename__ = "discovered_urls"
    __table_args__ = (UniqueConstraint("source_id", "normalized_url"),)
    id: Mapped[uuid.UUID] = _pk()
    normalized_url: Mapped[str] = mapped_column(Text)
    hostname: Mapped[str] = mapped_column(String(253), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("discovery_sources.id"), index=True)
    discovery_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("discovery_runs.id"), index=True)
    domain_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("domains.id"), index=True)
    first_discovered_at: Mapped[datetime] = _ts(index=True)
    last_discovered_at: Mapped[datetime] = _ts()
    processing_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)


class Domain(Base):
    """A candidate domain. Becomes a Store only after passing verification."""

    __tablename__ = "domains"
    # Backlog pick (monitoring tick): pending domains oldest-first without scanning all pending rows.
    __table_args__ = (
        Index("ix_domains_pending_first_seen", "first_seen_at",
              postgresql_where=text("verification_status = 'pending'"), sqlite_where=text("verification_status = 'pending'")),
    )  # fmt: skip
    id: Mapped[uuid.UUID] = _pk()
    normalized_domain: Mapped[str] = mapped_column(String(253), unique=True)
    first_seen_at: Mapped[datetime] = _ts(index=True)
    last_seen_at: Mapped[datetime] = _ts()
    current_resolution_status: Mapped[str | None] = mapped_column(String(20))
    # Latest verification outcome; full evidence per check lives in crawl_jobs.result.
    verification_status: Mapped[str] = mapped_column(String(30), default="pending", server_default="pending", index=True)
    verification_confidence: Mapped[float | None] = mapped_column(Float)
    last_checked_at: Mapped[datetime | None] = _opt_ts(index=True)
    # Opt-out / suppression (spec §16): never crawled again, hidden from the directory.
    suppressed_at: Mapped[datetime | None] = _opt_ts(index=True)
    suppression_reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _updated()


class Store(Base):
    __tablename__ = "stores"
    id: Mapped[uuid.UUID] = _pk()
    domain_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("domains.id"), unique=True)
    store_name: Mapped[str | None] = mapped_column(String(300))
    canonical_url: Mapped[str | None] = mapped_column(Text)
    ecommerce_status: Mapped[str] = mapped_column(String(30), index=True)
    activity_status: Mapped[str] = mapped_column(String(30), default="uncertain", index=True)
    activity_confidence: Mapped[float | None] = mapped_column(Float)
    activity_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_successful_check_at: Mapped[datetime | None] = _opt_ts()
    last_failed_check_at: Mapped[datetime | None] = _opt_ts()
    next_check_at: Mapped[datetime | None] = _opt_ts(index=True)
    last_changed_at: Mapped[datetime | None] = _opt_ts()  # activity / platform / catalogue change
    signals_refreshed_at: Mapped[datetime | None] = _opt_ts(index=True)
    primary_platform: Mapped[str] = mapped_column(String(30), default="unknown", index=True)
    platform_confidence: Mapped[float | None] = mapped_column(Float)
    first_verified_at: Mapped[datetime | None] = _opt_ts(index=True)
    last_verified_at: Mapped[datetime | None] = _opt_ts(index=True)
    last_observed_at: Mapped[datetime | None] = _opt_ts()
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _updated()


class PlatformDetection(Base):
    __tablename__ = "platform_detections"
    id: Mapped[uuid.UUID] = _pk()
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    platform: Mapped[str] = mapped_column(String(30), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    detection_method: Mapped[str] = mapped_column(String(50))
    detected_at: Mapped[datetime] = _ts()


class Product(Base):
    """Current state of a product as last observed. History lives in product_observations."""

    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("store_id", "product_url"),)
    id: Mapped[uuid.UUID] = _pk()
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    external_product_id: Mapped[str | None] = mapped_column(String(200))
    product_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    availability: Mapped[str | None] = mapped_column(String(30))
    image_url: Mapped[str | None] = mapped_column(Text)  # referenced, never downloaded
    sku: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active", index=True)
    extraction_method: Mapped[str | None] = mapped_column(String(30))  # json-ld | opengraph
    first_seen_at: Mapped[datetime] = _ts()
    last_seen_at: Mapped[datetime] = _ts(index=True)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _updated()


class ProductObservation(Base):
    """Append-only: written on first sight and on every tracked change (price, currency,
    availability, title), removal (404/410) and reappearance."""

    __tablename__ = "product_observations"
    __table_args__ = (Index("ix_product_observations_product_observed", "product_id", "observed_at"),)
    id: Mapped[uuid.UUID] = _pk()
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"))
    observed_at: Mapped[datetime] = _ts()
    change_type: Mapped[str] = mapped_column(String(20), index=True)  # new | changed | removed | reappeared
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    availability: Mapped[str | None] = mapped_column(String(30))
    title: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONType)  # {field: [old, new]} for changes


class StoreObservation(Base):
    __tablename__ = "store_observations"
    __table_args__ = (Index("ix_store_observations_store_observed", "store_id", "observed_at"),)
    id: Mapped[uuid.UUID] = _pk()
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"))
    observation_type: Mapped[str] = mapped_column(String(50), index=True)
    observation_data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    observed_at: Mapped[datetime] = _ts(index=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("discovery_sources.id"))


class StoreSignal(Base):
    """An evidence-based intelligence signal. One row per (store, type); resolved, never deleted."""

    __tablename__ = "store_signals"
    __table_args__ = (UniqueConstraint("store_id", "signal_type"), Index("ix_store_signals_type_active", "signal_type", "active"))
    id: Mapped[uuid.UUID] = _pk()
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"))
    signal_type: Mapped[str] = mapped_column(String(50))
    category: Mapped[str] = mapped_column(String(20), index=True)  # growth | change | opportunity | health
    basis: Mapped[str] = mapped_column(String(10))  # observed | inferred
    summary: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    first_detected_at: Mapped[datetime] = _ts()
    last_detected_at: Mapped[datetime] = _ts(index=True)
    resolved_at: Mapped[datetime | None] = _opt_ts()


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"
    __table_args__ = (
        Index("ix_crawl_jobs_status_scheduled", "status", "scheduled_at"),
        Index("ix_crawl_jobs_domain_type_status", "domain_id", "job_type", "status"),  # "in-flight job for domain?" checks
    )
    id: Mapped[uuid.UUID] = _pk()
    domain_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("domains.id"), index=True)
    target_domain: Mapped[str] = mapped_column(String(253), index=True)
    job_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=5)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_at: Mapped[datetime] = _ts()
    started_at: Mapped[datetime | None] = _opt_ts()
    completed_at: Mapped[datetime | None] = _opt_ts()
    error_message: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONType)  # classification + evidence


class CrawlPolicy(Base):
    __tablename__ = "crawl_policies"
    id: Mapped[uuid.UUID] = _pk()
    domain_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("domains.id"), unique=True)
    policy_status: Mapped[str] = mapped_column(String(20), default="unknown")  # allowed | restricted | blocked | unknown
    robots_txt_status: Mapped[int | None] = mapped_column(Integer)  # HTTP status of the robots.txt fetch
    robots_txt_fetched_at: Mapped[datetime | None] = _opt_ts()
    allowed_paths: Mapped[list[str]] = mapped_column(JSONType, default=list)
    disallowed_paths: Mapped[list[str]] = mapped_column(JSONType, default=list)
    crawl_delay_seconds: Mapped[float | None] = mapped_column(Float)
    max_requests_per_minute: Mapped[int] = mapped_column(Integer, default=10)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _updated()


class ContactRecord(Base):
    __tablename__ = "contact_records"
    id: Mapped[uuid.UUID] = _pk()
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    contact_type: Mapped[str] = mapped_column(String(30))  # email | phone | address | form
    value: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[datetime] = _ts()
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    suppressed_at: Mapped[datetime | None] = _opt_ts()


# ---------- commercial SaaS (M8): accounts, tenants, usage ----------


class Organization(Base):
    """A tenant. Owns saved searches, alerts, exports and usage; the store database is shared."""

    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200))
    plan: Mapped[str] = mapped_column(String(20), default="free", server_default="free")
    billing_customer_id: Mapped[str | None] = mapped_column(String(100), index=True)
    subscription_status: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _updated()


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    name: Mapped[str | None] = mapped_column(String(200))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())  # platform operator
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = _opt_ts()
    last_login_at: Mapped[datetime | None] = _opt_ts()
    created_at: Mapped[datetime] = _ts()


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "organization_id"),)
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="owner")  # owner | member
    created_at: Mapped[datetime] = _ts()


class AuthSession(Base):
    """Only the SHA-256 of the bearer token is stored."""

    __tablename__ = "auth_sessions"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = _ts()


class SavedSearch(Base):
    __tablename__ = "saved_searches"
    id: Mapped[uuid.UUID] = _pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(200))
    filters: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)  # store-directory filters
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_alert_check_at: Mapped[datetime | None] = _opt_ts()
    created_at: Mapped[datetime] = _ts()


class Alert(Base):
    """A store newly matching a saved search. In-app; email delivery needs SMTP (not configured)."""

    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("saved_search_id", "store_id"),)
    id: Mapped[uuid.UUID] = _pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    saved_search_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("saved_searches.id"))
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"))
    created_at: Mapped[datetime] = _ts(index=True)
    read_at: Mapped[datetime | None] = _opt_ts()


class UsageCounter(Base):
    __tablename__ = "usage_counters"
    __table_args__ = (UniqueConstraint("organization_id", "metric", "period"),)
    id: Mapped[uuid.UUID] = _pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    metric: Mapped[str] = mapped_column(String(50))  # exports | export_rows
    period: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    count: Mapped[int] = mapped_column(Integer, default=0)
