"""Store directory / store detail queries (spec §17 Store Directory + Store Details, §18 routes)."""

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import DiscoveredUrl, DiscoverySource, Domain, PlatformDetection, Product, Store, StoreObservation, StoreSignal

SORTS = {
    "newest": (Store.first_verified_at.desc().nulls_last(),),
    "oldest": (Store.first_verified_at.asc().nulls_last(),),
    "last_verified": (Store.last_verified_at.desc().nulls_last(),),
    "name": (func.lower(func.coalesce(Store.store_name, Domain.normalized_domain)).asc(),),
    "products": (func.coalesce(func.count(Product.id), 0).desc(),),
}


def list_stores(
    db: Session,
    *,
    q: str | None = None,
    platform: str | None = None,
    activity: str | None = None,
    ecommerce_status: str | None = None,
    signal: str | None = None,
    verified_from: datetime | None = None,
    verified_to: datetime | None = None,
    sort: str = "newest",
    page: int = 1,
    page_size: int = 50,
):
    products = func.count(Product.id).label("product_count")
    stmt = (
        select(Store, Domain.normalized_domain, products)
        .join(Domain, Domain.id == Store.domain_id)
        .outerjoin(Product, (Product.store_id == Store.id) & (Product.status == "active"))
        .where(Domain.suppressed_at.is_(None))
        .group_by(Store.id, Domain.normalized_domain)
    )
    if q:
        needle = q.strip().lower()
        stmt = stmt.where(
            Domain.normalized_domain.contains(needle, autoescape=True)
            | func.lower(func.coalesce(Store.store_name, "")).contains(needle, autoescape=True)
        )
    if platform:
        stmt = stmt.where(Store.primary_platform == platform)
    if activity:
        stmt = stmt.where(Store.activity_status == activity)
    if ecommerce_status:
        stmt = stmt.where(Store.ecommerce_status == ecommerce_status)
    if verified_from:
        stmt = stmt.where(Store.first_verified_at >= verified_from)
    if verified_to:
        stmt = stmt.where(Store.first_verified_at < verified_to)
    if signal:
        stmt = stmt.where(
            select(StoreSignal.id)
            .where(StoreSignal.store_id == Store.id, StoreSignal.signal_type == signal, StoreSignal.active.is_(True))
            .exists()
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(*SORTS[sort], Store.id).offset((page - 1) * page_size).limit(page_size)).all()
    return rows, total


def store_detail(db: Session, store_id: uuid.UUID):
    store = db.get(Store, store_id)
    if store is None:
        return None
    domain = db.get(Domain, store.domain_id)
    if domain.suppressed_at is not None:
        return None  # opted out: not shown
    product_count = db.scalar(select(func.count()).select_from(Product).where(Product.store_id == store.id, Product.status == "active"))
    detections = db.scalars(
        select(PlatformDetection).where(PlatformDetection.store_id == store.id).order_by(PlatformDetection.detected_at.desc()).limit(5)
    ).all()
    sig = db.scalars(
        select(StoreSignal).where(StoreSignal.store_id == store.id).order_by(StoreSignal.active.desc(), StoreSignal.last_detected_at.desc())
    ).all()
    sources = db.execute(
        select(DiscoverySource.name, DiscoverySource.source_type, func.min(DiscoveredUrl.first_discovered_at),
               func.max(DiscoveredUrl.last_discovered_at), func.count())
        .join(DiscoverySource, DiscoverySource.id == DiscoveredUrl.source_id)
        .where(DiscoveredUrl.domain_id == store.domain_id)
        .group_by(DiscoverySource.name, DiscoverySource.source_type)
    ).all()  # fmt: skip
    return {
        "store": store,
        "domain": domain.normalized_domain,
        "domain_id": domain.id,
        "domain_first_seen_at": domain.first_seen_at,
        "product_count": product_count or 0,
        "detections": detections,
        "signals": sig,
        "sources": [
            {"name": n, "source_type": t, "first_discovered_at": a, "last_discovered_at": b, "urls": c} for n, t, a, b, c in sources
        ],
    }


def observations(db: Session, store_id: uuid.UUID, kind: str | None, page: int, page_size: int):
    q = select(StoreObservation).where(StoreObservation.store_id == store_id)
    if kind:
        q = q.where(StoreObservation.observation_type == kind)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    items = db.scalars(q.order_by(StoreObservation.observed_at.desc(), StoreObservation.id).offset((page - 1) * page_size).limit(page_size)).all()
    return items, total


def platform_distribution(db: Session) -> list[dict]:
    q = (
        select(Store.primary_platform, func.count())
        .where(Store.ecommerce_status.in_(("confirmed_ecommerce", "probable_ecommerce")))
        .group_by(Store.primary_platform)
        .order_by(func.count().desc())
    )
    return [{"platform": p, "stores": n} for p, n in db.execute(q).all()]


def recent_changes(db: Session, limit: int = 15) -> list[dict]:
    kinds = ("activity_change", "platform_change", "signal_detected", "signal_resolved")
    q = (
        select(StoreObservation, Domain.normalized_domain)
        .join(Store, Store.id == StoreObservation.store_id)
        .join(Domain, Domain.id == Store.domain_id)
        .where(StoreObservation.observation_type.in_(kinds))
        .order_by(StoreObservation.observed_at.desc())
        .limit(limit)
    )
    return [
        {"store_id": o.store_id, "domain": d, "type": o.observation_type, "data": o.observation_data, "observed_at": o.observed_at}
        for o, d in db.execute(q).all()
    ]
