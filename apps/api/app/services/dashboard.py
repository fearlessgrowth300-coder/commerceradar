from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CrawlJob, DiscoveryRun, Domain, Store


def overview(db: Session, include_restricted: bool = False) -> dict[str, int]:
    def count(stmt) -> int:
        return db.scalar(stmt) or 0

    from app.services import provenance

    live = Store.merged_into_store_id.is_(None)  # duplicates folded into another store don't count twice
    if not include_restricted:
        live = live & ~provenance.restricted_store_clause()
    return {
        "candidate_domains": count(select(func.count()).select_from(Domain)),
        "verified_stores": count(select(func.count()).select_from(Store).where(live)),
        "active_stores": count(select(func.count()).where(live, Store.activity_status == "active")),
        "inactive_stores": count(select(func.count()).where(live, Store.activity_status == "inactive")),
        "new_stores_7d": count(select(func.count()).where(live, Store.first_verified_at >= datetime.now(UTC) - timedelta(days=7))),
        "discovery_runs": count(select(func.count()).select_from(DiscoveryRun)),
        "failed_jobs": count(select(func.count()).where(CrawlJob.status == "failed")),
    }
