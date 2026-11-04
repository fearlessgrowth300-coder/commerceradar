import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app import sources
from app.db import SessionLocal
from app.models import CrawlJob, DiscoveryRun, DiscoverySource
from app.services import discovery, verification
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


# ---------- discovery ----------

DISCOVERY_RETRIES = 5
CLAIM_TTL = timedelta(minutes=10)  # > task_time_limit, so a crashed batch can be picked up again


def _enqueue_verify(job_id) -> None:
    verify_domain.delay(str(job_id))


@celery_app.task(bind=True, name="discovery.run_batch", max_retries=DISCOVERY_RETRIES)
def run_discovery_batch(self, run_id: str, seq: int) -> str:
    """Process one batch of a discovery run, then chain the next one.

    `seq` makes the chain single-file: a redelivered or duplicated message whose seq no
    longer matches the run's cursor is dropped, so the cursor never advances twice.
    """
    rid = uuid.UUID(run_id)
    with SessionLocal() as db:
        run = db.get(DiscoveryRun, rid, with_for_update=True)
        if run is None or run.status not in discovery.ACTIVE_RUN or (run.cursor or {}).get("seq") != seq:
            db.rollback()
            return "skipped"
        source = db.get(DiscoverySource, run.source_id)
        adapter = sources.registry().get(source.source_type)
        if adapter is None or not source.enabled:
            run.status, run.completed_at = "failed", datetime.now(UTC)
            run.error_summary = "source disabled" if adapter else f"unknown source_type {source.source_type!r}"
            db.commit()
            return "failed"
        claim = (run.cursor or {}).get("claim")  # [seq, iso time] of a batch in progress
        if claim and claim[0] == seq and datetime.fromisoformat(claim[1]) > datetime.now(UTC) - CLAIM_TTL:
            db.rollback()
            return "skipped"  # another worker is on this exact batch
        run.status = "running"
        run.started_at = run.started_at or datetime.now(UTC)
        run.cursor = {**(run.cursor or {}), "claim": [seq, datetime.now(UTC).isoformat()]}
        db.commit()

        config = source.configuration or {}
        cursor = _unclaimed(run.cursor)
        state = dict(source.state or {})
        fetcher = SafeFetcher(max_bytes=adapter.max_bytes, timeout=adapter.timeout)
        try:
            batch = adapter.fetch_batch(config, cursor, state, sources.Ctx(fetcher, db))
        except FetchError as e:  # transient: same batch again later, cursor untouched
            db.rollback()
            run = db.get(DiscoveryRun, rid)
            if self.request.retries < DISCOVERY_RETRIES:
                run.status, run.error_summary = "retrying", str(e)[:2000]
                run.cursor = _unclaimed(run.cursor)  # the retry must be allowed to take this batch
                db.commit()
                raise self.retry(countdown=60 * 2**self.request.retries, exc=e)
            return _fail_run(db, rid, f"gave up after retries: {e}")
        except Exception as e:  # permanent (SourceError, bad config, bug): stop; cursor kept for resume
            db.rollback()
            log.exception("discovery run %s failed", rid)
            return _fail_run(db, rid, f"{type(e).__name__}: {e}")
        finally:
            fetcher.client.close()

        try:
            used = cursor.get("verify_queued", 0)
            budget = int(config.get("verify_new_limit", 100)) - used
            stats = discovery.ingest(db, source.id, run, batch.found, budget, _enqueue_verify)
        except Exception as e:
            db.rollback()
            log.exception("discovery ingest for run %s failed", rid)
            return _fail_run(db, rid, f"ingest {type(e).__name__}: {e}")

        run = db.get(DiscoveryRun, rid)
        source = db.get(DiscoverySource, run.source_id)
        source.state = state
        run.records_found += len(batch.found)
        run.records_processed += stats["urls"]
        run.records_failed += stats["invalid"]
        log_lines = [*(cursor.get("log") or [])[-19:], batch.note]  # last 20 batch notes
        if batch.cursor is None:
            run.status, run.completed_at = "succeeded", datetime.now(UTC)
            run.cursor = {**cursor, "seq": seq + 1, "log": log_lines, "verify_queued": used + stats["queued"], "done": True}
            run.error_summary = None
            db.commit()
            return "succeeded"
        run.cursor = {**batch.cursor, "seq": seq + 1, "log": log_lines, "verify_queued": used + stats["queued"]}
        run.error_summary = None
        db.commit()
        delay = float(config.get("interval_seconds", 5))
    run_discovery_batch.apply_async(args=[run_id, seq + 1], countdown=delay)
    return "continued"


def _fail_run(db: Session, rid: uuid.UUID, msg: str) -> str:
    run = db.get(DiscoveryRun, rid)
    run.status, run.completed_at, run.error_summary = "failed", datetime.now(UTC), msg[:2000]
    run.cursor = _unclaimed(run.cursor)  # keeps the position so the run can be resumed
    db.commit()
    return "failed"


def _unclaimed(cursor: dict | None) -> dict:
    return {k: v for k, v in (cursor or {}).items() if k != "claim"}
