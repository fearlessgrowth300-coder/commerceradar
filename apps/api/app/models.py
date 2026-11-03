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
    Uuid,
    func,
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


class DiscoveredUrl(Base):
    # ponytail: no URL-level unique constraint yet; dedup (hash of normalized_url) lands with source adapters in M4.
    __tablename__ = "discovered_urls"
    id: Mapped[uuid.UUID] = _pk()
    normalized_url: Mapped[str] = mapped_column(Text)
    hostname: Mapped[str] = mapped_column(String(253), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("discovery_sources.id"), index=True)
    discovery_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("discovery_runs.id"), index=True)
    first_discovered_at: Mapped[datetime] = _ts(index=True)
    last_discovered_at: Mapped[datetime] = _ts()
    processing_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)


class Domain(Base):
    """A candidate domain. Becomes a Store only after passing verification."""

    __tablename__ = "domains"
    id: Mapped[uuid.UUID] = _pk()
    normalized_domain: Mapped[str] = mapped_column(String(253), unique=True)
    first_seen_at: Mapped[datetime] = _ts(index=True)
    last_seen_at: Mapped[datetime] = _ts()
    current_resolution_status: Mapped[str | None] = mapped_column(String(20))
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
    __tablename__ = "products"
    id: Mapped[uuid.UUID] = _pk()
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"), index=True)
    external_product_id: Mapped[str | None] = mapped_column(String(200))
    product_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    availability: Mapped[str | None] = mapped_column(String(30))
    image_url: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = _ts()
    last_seen_at: Mapped[datetime] = _ts()
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _updated()


class StoreObservation(Base):
    __tablename__ = "store_observations"
    __table_args__ = (Index("ix_store_observations_store_observed", "store_id", "observed_at"),)
    id: Mapped[uuid.UUID] = _pk()
    store_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stores.id"))
    observation_type: Mapped[str] = mapped_column(String(50), index=True)
    observation_data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    observed_at: Mapped[datetime] = _ts(index=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("discovery_sources.id"))


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"
    __table_args__ = (Index("ix_crawl_jobs_status_scheduled", "status", "scheduled_at"),)
    id: Mapped[uuid.UUID] = _pk()
    target_domain: Mapped[str] = mapped_column(String(253), index=True)
    job_type: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=5)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_at: Mapped[datetime] = _ts()
    started_at: Mapped[datetime | None] = _opt_ts()
    completed_at: Mapped[datetime | None] = _opt_ts()
    error_message: Mapped[str | None] = mapped_column(Text)


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
