"""Store directory / store detail queries (spec §17 Store Directory + Store Details, §18 routes)."""

import uuid
from datetime import datetime

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from app.services import provenance
from app.models import (DiscoveredUrl, DiscoverySource, Domain, ExportedStore, PlatformDetection, Product, Store,
                        StoreObservation, StoreSignal)

FORMAT_COLUMNS = {"pod": Store.is_pod, "cod": Store.is_cod, "dropshipping": Store.is_dropshipping, "digital": Store.is_digital}
SORTS = {
    "newest": (Store.first_verified_at.desc().nulls_last(),),
    "oldest": (Store.first_verified_at.asc().nulls_last(),),
    "last_verified": (Store.last_verified_at.desc().nulls_last(),),
    "name": (func.lower(func.coalesce(Store.store_name, Domain.normalized_domain)).asc(),),
    "products": (),  # ordered by the per-store product count, see list_stores
    "latest_product": (Store.latest_product_at.desc().nulls_last(),),
    "store_created": (Store.first_product_at.desc().nulls_last(),),
}


def list_stores(
    db: Session,
    *,
    q: str | None = None,
    platform: str | None = None,
    activity: str | None = None,
    country: str | None = None,
    ecommerce_status: str | None = None,
    signal: str | None = None,
    verified_from: datetime | None = None,
    verified_to: datetime | None = None,
    sort: str = "newest",
    page: int = 1,
    page_size: int = 50,
    include_restricted: bool = False,
    not_exported_by: uuid.UUID | None = None,  # organization id: skip stores it already downloaded
    language: str | None = None,
    category: str | None = None,  # a group ("Pet Supplies") or a subcategory ("Pet Toys")
    format: str | None = None,  # pod | cod | dropshipping | digital
    first_product_from: datetime | None = None,
    first_product_to: datetime | None = None,
    latest_product_from: datetime | None = None,
    latest_product_to: datetime | None = None,
    with_total: bool = True,  # exports already know the total: skip re-counting every match per page
):
    # Products are counted per returned store (correlated subquery on products.store_id), not joined and grouped:
    # the join aggregated every product of every store (2.6 M rows) just to show one page.
    products = (select(func.count(Product.id)).where(Product.store_id == Store.id, Product.status == "active")
                .correlate(Store).scalar_subquery())  # fmt: skip
    stmt = (
        select(Store, Domain.normalized_domain)
        .join(Domain, Domain.id == Store.domain_id)
        .where(Domain.suppressed_at.is_(None), Store.merged_into_store_id.is_(None))
    )
    if not include_restricted:
        stmt = stmt.where(~provenance.restricted_store_clause())
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
    if country:
        stmt = stmt.where(Store.business_country == country.upper())
    if not_exported_by:
        stmt = stmt.where(
            ~select(ExportedStore.id)
            .where(ExportedStore.organization_id == not_exported_by, ExportedStore.store_id == Store.id)
            .exists()
        )
    if language:
        stmt = stmt.where(Store.language == language.lower())
    if category:
        stmt = stmt.where((Store.category_group == category) | (Store.category == category))
    if format:
        stmt = stmt.where(FORMAT_COLUMNS[format].is_(True))
    for col, lo, hi in ((Store.first_product_at, first_product_from, first_product_to),
                        (Store.latest_product_at, latest_product_from, latest_product_to)):
        if lo:
            stmt = stmt.where(col >= lo)
        if hi:
            stmt = stmt.where(col < hi)
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
    total = (db.scalar(select(func.count()).select_from(stmt.subquery())) or 0) if with_total else None
    if sort == "products" or page_size > 1000:  # every/many stores' counts: one grouped pass beats a count per store
        counts = (select(Product.store_id, func.count().label("n")).where(Product.status == "active")
                  .group_by(Product.store_id).subquery())  # fmt: skip
        n = func.coalesce(counts.c.n, 0)
        page_q = stmt.outerjoin(counts, counts.c.store_id == Store.id).add_columns(n.label("product_count")).order_by(n.desc(), Store.id)
    else:
        page_q = stmt.add_columns(products.label("product_count")).order_by(*SORTS[sort], Store.id)
    rows = db.execute(page_q.offset((page - 1) * page_size).limit(page_size)).all()
    return rows, total


def store_detail(db: Session, store_id: uuid.UUID, include_restricted: bool = False):
    store = db.get(Store, store_id)
    if store is None:
        return None
    restricted = provenance.is_restricted(db, store.id)
    if restricted and not include_restricted:
        return None
    domain = db.get(Domain, store.domain_id)
    if domain.suppressed_at is not None or store.merged_into_store_id is not None:
        return None  # opted out, or folded into another store
    from app.models import StoreDomain

    aliases = db.execute(
        select(Domain.normalized_domain, StoreDomain.role, StoreDomain.alias_type, StoreDomain.confidence,
               StoreDomain.first_observed_at, StoreDomain.last_observed_at, StoreDomain.valid_to)
        .join(Domain, Domain.id == StoreDomain.domain_id).where(StoreDomain.store_id == store.id)
        .order_by(StoreDomain.valid_to.is_not(None), StoreDomain.role, Domain.normalized_domain)
    ).all()  # fmt: skip
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
    first_cert = db.scalar(
        select(func.min(DiscoveredUrl.first_discovered_at))
        .join(DiscoverySource, DiscoverySource.id == DiscoveredUrl.source_id)
        .where(DiscoveredUrl.domain_id == store.domain_id, DiscoverySource.source_type == "crtsh")
    )
    return {
        "first_certificate_observed_at": first_cert,
        "restricted": restricted,
        "store": store,
        "domain": domain.normalized_domain,
        "domain_id": domain.id,
        "domain_first_seen_at": domain.first_seen_at,
        "product_count": product_count or 0,
        "detections": detections,
        "signals": sig,
        "domains": [
            {"domain": a, "role": r, "alias_type": t, "confidence": c, "first_observed_at": f, "last_observed_at": l,
             "current": v is None}
            for a, r, t, c, f, l, v in aliases
        ],  # fmt: skip
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


def platform_distribution(db: Session, include_restricted: bool = False) -> list[dict]:
    q = (
        select(Store.primary_platform, func.count())
        .join(Domain, Domain.id == Store.domain_id)
        .where(Store.ecommerce_status.in_(("confirmed_ecommerce", "probable_ecommerce")), Domain.suppressed_at.is_(None),
               Store.merged_into_store_id.is_(None))
        .where(true() if include_restricted else ~provenance.restricted_store_clause())
        .group_by(Store.primary_platform)
        .order_by(func.count().desc())
    )
    return [{"platform": p, "stores": n} for p, n in db.execute(q).all()]


def recent_changes(db: Session, limit: int = 15, include_restricted: bool = False) -> list[dict]:
    kinds = ("activity_change", "platform_change", "signal_detected", "signal_resolved")
    q = (
        select(StoreObservation, Domain.normalized_domain)
        .join(Store, Store.id == StoreObservation.store_id)
        .join(Domain, Domain.id == Store.domain_id)
        .where(StoreObservation.observation_type.in_(kinds), Domain.suppressed_at.is_(None))
        .where(true() if include_restricted else ~provenance.restricted_store_clause())
        .order_by(StoreObservation.observed_at.desc())
        .limit(limit)
    )
    return [
        {"store_id": o.store_id, "domain": d, "type": o.observation_type, "data": o.observation_data, "observed_at": o.observed_at}
        for o, d in db.execute(q).all()
    ]


def visible_store(db: Session, store_id: uuid.UUID, include_restricted: bool = False) -> Store | None:
    """The store, unless it does not exist or its domain opted out."""
    store = db.get(Store, store_id)
    if store is None:
        return None
    if store.merged_into_store_id is not None:
        return None
    if not include_restricted and provenance.is_restricted(db, store.id):
        return None
    return None if db.scalar(select(Domain.suppressed_at).where(Domain.id == store.domain_id)) else store
