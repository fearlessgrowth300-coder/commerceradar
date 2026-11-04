import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import Ctx, admin, current
from app.db import get_db
from app.models import CrawlJob, DiscoveryRun, DiscoverySource, Product, Store
from app.services import dashboard, discovery, domains, monitoring, products, provenance, signals, stores

router = APIRouter(prefix="/api/v1", dependencies=[Depends(current)])  # every route needs a session


class Overview(BaseModel):
    candidate_domains: int
    verified_stores: int
    active_stores: int
    inactive_stores: int
    new_stores_7d: int
    discovery_runs: int
    failed_jobs: int


class DomainSubmit(BaseModel):
    domain: str = Field(min_length=1, max_length=2000)


class DomainOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    normalized_domain: str
    verification_status: str
    verification_confidence: float | None
    current_resolution_status: str | None
    first_seen_at: datetime
    last_checked_at: datetime | None


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    job_type: str
    status: str
    attempts: int
    scheduled_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    result: dict[str, Any] | None


class SubmitOut(BaseModel):
    domain: DomainOut
    job: JobOut
    created: bool


class DomainPage(BaseModel):
    items: list[DomainOut]
    total: int
    page: int
    page_size: int


class DomainDetail(BaseModel):
    domain: DomainOut
    jobs: list[JobOut]
    store_id: uuid.UUID | None
    platform: str | None  # store's primary_platform; None when the domain isn't a verified store
    platform_confidence: float | None
    activity: dict[str, Any] | None = None  # store activity status + evidence + check schedule


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    source_id: uuid.UUID
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    records_found: int
    records_processed: int
    records_failed: int
    error_summary: str | None
    cursor: dict[str, Any] | None  # includes "log" (last batch notes) and "verify_queued"


class SourceOut(BaseModel):
    id: uuid.UUID
    name: str
    source_type: str
    enabled: bool
    configuration: dict[str, Any]
    urls_discovered: int
    last_run: RunOut | None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    store_id: uuid.UUID
    product_url: str
    title: str | None
    description: str | None
    image_url: str | None
    sku: str | None
    category: str | None
    current_price: Decimal | None
    currency: str | None
    availability: str | None
    status: str
    extraction_method: str | None
    first_seen_at: datetime
    last_seen_at: datetime


class ProductPage(BaseModel):
    items: list[ProductOut]
    total: int
    page: int
    page_size: int


class ObservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    observed_at: datetime
    change_type: str
    price: Decimal | None
    currency: str | None
    availability: str | None
    title: str | None
    details: dict[str, Any] | None


class ProductDetail(BaseModel):
    product: ProductOut
    history: list[ObservationOut]


def _enqueue_products(job_id: uuid.UUID) -> None:
    from app.tasks import extract_products

    extract_products.delay(str(job_id))


def _enqueue_run(run_id: uuid.UUID, seq: int) -> None:
    from app.tasks import run_discovery_batch

    run_discovery_batch.delay(str(run_id), seq)


def _enqueue(job_id: uuid.UUID) -> None:
    from app.tasks import verify_domain

    verify_domain.delay(str(job_id))


@router.get("/dashboard/overview", response_model=Overview)
def dashboard_overview(db: Session = Depends(get_db), ctx: Ctx = Depends(current)):
    return dashboard.overview(db, include_restricted=ctx.user.is_admin)


@router.post("/discovery/domains", response_model=SubmitOut, status_code=202, dependencies=[Depends(admin)])
def submit_domain(body: DomainSubmit, db: Session = Depends(get_db)):
    try:
        domain, job, created = domains.submit_domain(db, body.domain, _enqueue)
    except domains.InvalidDomain as e:
        raise HTTPException(422, str(e)) from e
    except domains.QueueUnavailable as e:
        raise HTTPException(503, str(e)) from e
    return {"domain": domain, "job": job, "created": created}


@router.get("/domains", response_model=DomainPage)
def list_domains(
    db: Session = Depends(get_db),
    status: str | None = Query(None, description="verification status"),
    q: str | None = Query(None, max_length=253),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    if status and status not in domains.VERIFICATION_STATUSES:
        raise HTTPException(422, f"status must be one of {', '.join(domains.VERIFICATION_STATUSES)}")
    items, total = domains.list_domains(db, status, q, page, page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/domains/{domain_id}", response_model=DomainDetail)
def get_domain(domain_id: uuid.UUID, db: Session = Depends(get_db)):
    found = domains.get_domain_detail(db, domain_id)
    if not found:
        raise HTTPException(404, "domain not found")
    domain, jobs, store = found
    return {
        "domain": domain,
        "jobs": jobs,
        "store_id": store.id if store else None,
        "platform": store.primary_platform if store else None,
        "platform_confidence": store.platform_confidence if store else None,
        "activity": (
            {k: getattr(store, k) for k in ("activity_status", "activity_confidence", "activity_evidence",
             "consecutive_failures", "last_successful_check_at", "last_failed_check_at", "next_check_at", "last_changed_at")}
            if store else None
        ),  # fmt: skip
    }


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    job = db.get(CrawlJob, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


@router.get("/discovery/sources", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_db)):
    return [
        {**{k: getattr(src, k) for k in ("id", "name", "source_type", "enabled")},
         "configuration": src.configuration or {}, "urls_discovered": urls, "last_run": last}
        for src, last, urls in discovery.list_sources(db)
    ]  # fmt: skip


@router.post("/discovery/sources/{source_id}/runs", response_model=RunOut, status_code=202, dependencies=[Depends(admin)])
def start_source_run(source_id: uuid.UUID, db: Session = Depends(get_db)):
    source = db.get(DiscoverySource, source_id)
    if not source:
        raise HTTPException(404, "source not found")
    try:
        return discovery.launch_run(db, source, _enqueue_run)
    except discovery.RunConflict as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:
        raise HTTPException(503, "queue unavailable") from e


@router.get("/discovery/runs", response_model=list[RunOut])
def list_runs(
    db: Session = Depends(get_db), source_id: uuid.UUID | None = None, limit: int = Query(20, ge=1, le=100)
):
    return discovery.list_runs(db, source_id, limit)


@router.post("/discovery/runs/{run_id}/resume", response_model=RunOut, status_code=202, dependencies=[Depends(admin)])
def resume_run(run_id: uuid.UUID, db: Session = Depends(get_db)):
    run = db.get(DiscoveryRun, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    try:
        return discovery.resume_run(db, run, _enqueue_run)
    except discovery.RunConflict as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:
        raise HTTPException(503, "queue unavailable") from e


@router.post("/stores/{store_id}/products/extract", response_model=JobOut, status_code=202, dependencies=[Depends(admin)])
def extract_store_products(store_id: uuid.UUID, db: Session = Depends(get_db)):
    store = db.get(Store, store_id)
    if not store:
        raise HTTPException(404, "store not found")
    try:
        job, _ = products.queue_extraction(db, store, _enqueue_products)
    except products.NotExtractable as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:
        raise HTTPException(503, "queue unavailable") from e
    return job


@router.get("/stores/{store_id}/products", response_model=ProductPage)
def list_store_products(
    store_id: uuid.UUID,
    db: Session = Depends(get_db),
    status: str | None = Query(None, pattern="^(active|removed)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    ctx: Ctx = Depends(current),
):
    if not stores.visible_store(db, store_id, include_restricted=ctx.user.is_admin):
        raise HTTPException(404, "store not found")
    items, total = products.list_products(db, store_id, status, page, page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/products/{product_id}", response_model=ProductDetail)
def get_product(product_id: uuid.UUID, db: Session = Depends(get_db), ctx: Ctx = Depends(current)):
    product = db.get(Product, product_id)
    if not product or not stores.visible_store(db, product.store_id, include_restricted=ctx.user.is_admin):
        raise HTTPException(404, "product not found")
    return {"product": product, "history": products.product_history(db, product_id)}


@router.get("/monitoring/status")
def monitoring_status(db: Session = Depends(get_db)):
    return monitoring.status(db, datetime.now(UTC))


@router.post("/monitoring/tick", status_code=202, dependencies=[Depends(admin)])
def monitoring_tick_now():
    """Run one scheduler tick now instead of waiting for beat."""
    from app.tasks import monitoring_tick

    try:
        monitoring_tick.delay()
    except Exception as e:
        raise HTTPException(503, "queue unavailable") from e
    return {"queued": True}


# ---------- store directory, details, intelligence (M7) ----------

ACTIVITY = ("active", "recently_active", "inactive", "uncertain")
PLATFORMS = ("shopify", "woocommerce", "wix", "bigcommerce", "magento", "prestashop", "squarespace", "opencart", "custom", "unknown")


class StoreRow(BaseModel):
    id: uuid.UUID
    domain: str
    store_name: str | None
    primary_platform: str
    platform_confidence: float | None
    ecommerce_status: str
    activity_status: str
    first_verified_at: datetime | None
    last_verified_at: datetime | None
    product_count: int
    restricted: bool = False


class StorePage(BaseModel):
    items: list[StoreRow]
    total: int
    page: int
    page_size: int


class SignalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    signal_type: str
    category: str
    basis: str
    summary: str
    evidence: dict[str, Any]
    active: bool
    first_detected_at: datetime
    last_detected_at: datetime
    resolved_at: datetime | None


class DetectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    platform: str
    confidence: float
    evidence: dict[str, Any]
    detection_method: str
    detected_at: datetime


class StoreFull(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    store_name: str | None
    canonical_url: str | None
    ecommerce_status: str
    activity_status: str
    activity_confidence: float | None
    activity_evidence: dict[str, Any] | None
    primary_platform: str
    platform_confidence: float | None
    first_verified_at: datetime | None
    last_verified_at: datetime | None
    last_observed_at: datetime | None
    last_successful_check_at: datetime | None
    last_failed_check_at: datetime | None
    next_check_at: datetime | None
    last_changed_at: datetime | None
    consecutive_failures: int


class StoreDetailOut(BaseModel):
    store: StoreFull
    domain: str
    domain_id: uuid.UUID
    domain_first_seen_at: datetime
    product_count: int
    restricted: bool = False  # admins only ever see True: found only by a source whose usage rights are not cleared
    first_certificate_observed_at: datetime | None = None  # CT evidence; not a launch date
    domains: list[dict[str, Any]] = []
    detections: list[DetectionOut]
    signals: list[SignalOut]
    sources: list[dict[str, Any]]


class StoreObservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    observation_type: str
    observation_data: dict[str, Any]
    observed_at: datetime


class ObservationPage(BaseModel):
    items: list[StoreObservationOut]
    total: int
    page: int
    page_size: int


class SignalRow(SignalOut):
    store_id: uuid.UUID
    domain: str
    store_name: str | None


class SignalPage(BaseModel):
    items: list[SignalRow]
    total: int
    page: int
    page_size: int


def _store_filters(platform, activity, signal) -> None:
    if platform and platform not in PLATFORMS:
        raise HTTPException(422, f"platform must be one of {', '.join(PLATFORMS)}")
    if activity and activity not in ACTIVITY:
        raise HTTPException(422, f"activity must be one of {', '.join(ACTIVITY)}")
    if signal and signal not in signals.SIGNAL_TYPES:
        raise HTTPException(422, f"signal must be one of {', '.join(signals.SIGNAL_TYPES)}")


def _store_rows(rows) -> list[dict]:
    return [
        {"id": st.id, "domain": dom, "store_name": st.store_name, "primary_platform": st.primary_platform,
         "platform_confidence": st.platform_confidence, "ecommerce_status": st.ecommerce_status,
         "activity_status": st.activity_status, "first_verified_at": st.first_verified_at,
         "last_verified_at": st.last_verified_at, "product_count": n}
        for st, dom, n in rows
    ]  # fmt: skip


@router.get("/stores", response_model=StorePage)
def list_stores(
    db: Session = Depends(get_db),
    q: str | None = Query(None, max_length=253),
    platform: str | None = Query(None),
    activity: str | None = Query(None),
    ecommerce_status: str | None = Query(None, pattern="^(confirmed_ecommerce|probable_ecommerce)$"),
    signal: str | None = Query(None),
    verified_from: datetime | None = None,
    verified_to: datetime | None = None,
    sort: str = Query("newest", pattern="^(newest|oldest|last_verified|name|products)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    ctx: Ctx = Depends(current),
):
    _store_filters(platform, activity, signal)
    admin_view = ctx.user.is_admin
    rows, total = stores.list_stores(
        db, q=q, platform=platform, activity=activity, ecommerce_status=ecommerce_status, signal=signal,
        verified_from=verified_from, verified_to=verified_to, sort=sort, page=page, page_size=page_size,
        include_restricted=admin_view,
    )  # fmt: skip
    items = _store_rows(rows)
    flagged = provenance.restricted_ids(db, [r["id"] for r in items]) if admin_view else set()
    for r in items:
        r["restricted"] = r["id"] in flagged
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/stores/{store_id}", response_model=StoreDetailOut)
def get_store(store_id: uuid.UUID, db: Session = Depends(get_db), ctx: Ctx = Depends(current)):
    found = stores.store_detail(db, store_id, include_restricted=ctx.user.is_admin)
    if not found:
        raise HTTPException(404, "store not found")
    return found


@router.get("/stores/{store_id}/observations", response_model=ObservationPage)
def store_observations(
    store_id: uuid.UUID,
    db: Session = Depends(get_db),
    type: str | None = Query(None, max_length=50),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    ctx: Ctx = Depends(current),
):
    if not stores.visible_store(db, store_id, include_restricted=ctx.user.is_admin):
        raise HTTPException(404, "store not found")
    items, total = stores.observations(db, store_id, type, page, page_size)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/platforms")
def platform_distribution(db: Session = Depends(get_db), ctx: Ctx = Depends(current)):
    return stores.platform_distribution(db, include_restricted=ctx.user.is_admin)


@router.get("/signals", response_model=SignalPage)
def list_signals(
    db: Session = Depends(get_db),
    type: str | None = Query(None),
    category: str | None = Query(None, pattern="^(growth|change|opportunity|health)$"),
    active: bool = True,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    ctx: Ctx = Depends(current),
):
    if type and type not in signals.SIGNAL_TYPES:
        raise HTTPException(422, f"type must be one of {', '.join(signals.SIGNAL_TYPES)}")
    rows, total = signals.list_signals(db, type, category, active, page, page_size, include_restricted=ctx.user.is_admin)
    items = [
        {**SignalOut.model_validate(sg).model_dump(), "store_id": sg.store_id, "domain": dom, "store_name": name}
        for sg, dom, name in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/signals/types")
def signal_types(db: Session = Depends(get_db)):
    active = signals.counts(db)
    return [{"type": t, "category": c, "basis": b, "active": active.get(t, 0)} for t, (c, b) in signals.SIGNAL_TYPES.items()]


@router.get("/dashboard/activity")
def dashboard_activity(db: Session = Depends(get_db), ctx: Ctx = Depends(current)):
    adm = ctx.user.is_admin
    return {
        "platforms": stores.platform_distribution(db, include_restricted=adm),
        "recent_runs": [RunOut.model_validate(r).model_dump() for r in discovery.list_runs(db, None, 5)],
        "recent_changes": stores.recent_changes(db, include_restricted=adm),
        "signals": signals.counts(db),
    }


class RunStart(BaseModel):
    source_id: uuid.UUID


@router.post("/discovery/runs", response_model=RunOut, status_code=202, dependencies=[Depends(admin)])
def start_run(body: RunStart, db: Session = Depends(get_db)):
    return start_source_run(body.source_id, db)


@router.get("/discovery/runs/{run_id}", response_model=RunOut)
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db)):
    run = db.get(DiscoveryRun, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run
