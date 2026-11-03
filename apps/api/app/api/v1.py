from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import dashboard

router = APIRouter(prefix="/api/v1")


class Overview(BaseModel):
    candidate_domains: int
    verified_stores: int
    active_stores: int
    inactive_stores: int
    discovery_runs: int
    failed_jobs: int


@router.get("/dashboard/overview", response_model=Overview)
def dashboard_overview(db: Session = Depends(get_db)):
    return dashboard.overview(db)
