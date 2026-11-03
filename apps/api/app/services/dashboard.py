from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CrawlJob, DiscoveryRun, Domain, Store


def overview(db: Session) -> dict[str, int]:
    def count(stmt) -> int:
        return db.scalar(stmt) or 0

    return {
        "candidate_domains": count(select(func.count()).select_from(Domain)),
        "verified_stores": count(select(func.count()).select_from(Store)),
        "active_stores": count(select(func.count()).where(Store.activity_status == "active")),
        "inactive_stores": count(select(func.count()).where(Store.activity_status == "inactive")),
        "discovery_runs": count(select(func.count()).select_from(DiscoveryRun)),
        "failed_jobs": count(select(func.count()).where(CrawlJob.status == "failed")),
    }
