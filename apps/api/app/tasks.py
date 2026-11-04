import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import CrawlJob
from app.services import verification
from app.services.fetcher import FetchError, SafeFetcher
from app.worker import celery_app

log = logging.getLogger(__name__)
MAX_RETRIES = 3


def _fail(db: Session, job_id: uuid.UUID, e: Exception) -> str:
    log.exception("verification job %s failed", job_id)
    db.rollback()
    job = db.get(CrawlJob, job_id)
    job.status = "failed"
    job.completed_at = datetime.now(UTC)
    job.error_message = f"{type(e).__name__}: {e}"[:2000]
    db.commit()
    return "failed"


@celery_app.task(bind=True, name="verification.verify_domain", max_retries=MAX_RETRIES)
def verify_domain(self, job_id: str) -> str:
    jid = uuid.UUID(job_id)
    with SessionLocal() as db:
        job = db.get(CrawlJob, jid)
        if job is None or job.status in ("succeeded", "failed"):
            return "skipped"  # idempotent: redelivered or duplicate message
        job.status = "running"
        job.started_at = job.started_at or datetime.now(UTC)
        job.attempts += 1
        db.commit()

        fetcher = SafeFetcher()
        try:
            result = verification.verify(job.target_domain, fetcher)
        except FetchError as e:
            if self.request.retries < MAX_RETRIES:
                job.status = "retrying"
                job.error_message = str(e)
                db.commit()
                raise self.retry(countdown=30 * 2**self.request.retries, exc=e)
            result = verification.VerificationResult(
                "temporarily_unreachable", 0.0, "error", {"reason": str(e), "attempts": job.attempts}
            )
        except Exception as e:  # one bad site must never crash the worker
            return _fail(db, jid, e)
        finally:
            fetcher.client.close()

        try:
            verification.apply_result(db, job, result)
        except Exception as e:
            return _fail(db, jid, e)
        return result.status
