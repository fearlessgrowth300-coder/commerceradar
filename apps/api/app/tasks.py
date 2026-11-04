import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import sources
from app.db import SessionLocal
from app.config import settings
from app.models import CrawlJob, DiscoveryRun, DiscoverySource, Store
from app.services import discovery, products, suppression, verification
from app.services.fetcher import FetchError, SafeFetcher
from app.worker import celery_app

log = logging.getLogger(__name__)
MAX_RETRIES = 3


def _suppressed(db: Session, domain_id) -> bool:
    from app.models import Domain

    d = db.get(Domain, domain_id) if domain_id else None
    return bool(d and (d.suppressed_at or suppression.is_suppressed(db, d.normalized_domain)))


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
        if _suppressed(db, job.domain_id):
            job.status, job.completed_at, job.error_message = "failed", datetime.now(UTC), "domain suppressed (opt-out)"
            db.commit()
            return "suppressed"
        job.status = "running"
        job.started_at = job.started_at or datetime.now(UTC)
        job.attempts += 1
        db.commit()

        fetcher = SafeFetcher()
        fetcher.blocked = suppression.guard(db)
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

        # Product monitoring rides on every positive store check (new or re-check).
        if settings.auto_extract_products and result.status in products.EXTRACTABLE:
            store = db.scalar(select(Store).where(Store.domain_id == job.domain_id))
            try:
                products.queue_extraction(db, store, _enqueue_products)
            except Exception:
                log.warning("could not queue product extraction for %s", job.target_domain)
        return result.status


def _enqueue_products(job_id) -> None:
    extract_products.delay(str(job_id))


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
        fetcher.blocked = suppression.guard(db)
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
        cursor["touched"] = datetime.now(UTC).isoformat()  # stuck-run detection
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


# ---------- products ----------


@celery_app.task(bind=True, name="products.extract_store", max_retries=MAX_RETRIES)
def extract_products(self, job_id: str) -> str:
    """Idempotent per job; re-running only re-observes (history rows are written on change)."""
    jid = uuid.UUID(job_id)
    with SessionLocal() as db:
        job = db.get(CrawlJob, jid)
        if job is None or job.status in ("succeeded", "failed"):
            return "skipped"
        store = db.scalar(select(Store).where(Store.domain_id == job.domain_id))
        if store is None:
            return _fail(db, jid, ValueError("no verified store for this domain"))
        if _suppressed(db, job.domain_id):
            return _fail(db, jid, ValueError("domain suppressed (opt-out)"))
        job.status, job.started_at = "running", job.started_at or datetime.now(UTC)
        job.attempts += 1
        db.commit()

        fetcher = SafeFetcher()
        fetcher.blocked = suppression.guard(db)
        try:
            stats = products.extract_store(db, store, fetcher, settings.product_pages_per_run, settings.product_page_delay)
        except FetchError as e:
            db.rollback()
            job = db.get(CrawlJob, jid)
            if self.request.retries < MAX_RETRIES:
                job.status, job.error_message = "retrying", str(e)
                db.commit()
                raise self.retry(countdown=60 * 2**self.request.retries, exc=e)
            return _fail(db, jid, e)
        except Exception as e:
            return _fail(db, jid, e)
        finally:
            fetcher.client.close()

        job = db.get(CrawlJob, jid)
        job.status, job.completed_at, job.error_message = "succeeded", datetime.now(UTC), None
        job.result = {"status": "extracted", "evidence": stats.__dict__}
        db.commit()
        return "succeeded"


# ---------- monitoring ----------


@celery_app.task(name="monitoring.tick")
def monitoring_tick() -> dict:
    """Beat-driven. Bounded work per call, safe to run twice (in-flight checks dedupe)."""
    from app.services import monitoring

    with SessionLocal() as db:
        result = monitoring.tick(
            db,
            datetime.now(UTC),
            enqueue_verify=lambda jid: verify_domain.delay(str(jid)),
            enqueue_products=_enqueue_products,
            enqueue_run=lambda rid, seq: run_discovery_batch.delay(str(rid), seq),
        )
    log.info("monitoring tick: %s", result)
    return result
