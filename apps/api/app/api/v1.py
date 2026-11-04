import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CrawlJob
from app.services import dashboard, domains

router = APIRouter(prefix="/api/v1")


class Overview(BaseModel):
    candidate_domains: int
    verified_stores: int
    active_stores: int
    inactive_stores: int
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


def _enqueue(job_id: uuid.UUID) -> None:
    from app.tasks import verify_domain

    verify_domain.delay(str(job_id))


@router.get("/dashboard/overview", response_model=Overview)
def dashboard_overview(db: Session = Depends(get_db)):
    return dashboard.overview(db)


@router.post("/discovery/domains", response_model=SubmitOut, status_code=202)
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
    }


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db)):
    job = db.get(CrawlJob, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job
