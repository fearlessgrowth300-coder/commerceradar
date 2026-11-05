"""Continuous monitoring (spec §11, §15).

Activity classification — one failed request never closes a store:
  positive check (confirmed/probable)        → active, failures reset
  inconclusive (bot wall, robots, timeouts)  → recently_active if it was active, else uncertain;
                                               inactive only after many failures AND a long time without success
  negative (not ecommerce any more, domain gone / non-public) → inactive after N consecutive negatives
Every status change is written to store_observations (history is never overwritten).

Scheduling: `tick()` runs from Celery beat. Each tick does a small, bounded amount of work so
the VPS stays responsive: re-check due stores, chip away at the unverified backlog, re-check
stale uncertain candidates, start scheduled discovery sources, recover lost jobs/runs.
"""

import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import CrawlJob, DiscoveryRun, DiscoverySource, Domain, Store, StoreObservation

POSITIVE = ("confirmed_ecommerce", "probable_ecommerce")
IN_FLIGHT = ("queued", "running", "retrying")
ACTIVITY_STATUSES = ("active", "recently_active", "inactive", "uncertain")


def failure_kind(status: str, resolution: str) -> str | None:
    """None for a positive check; else negative | transient | inconclusive."""
    if status in POSITIVE:
        return None
    if status in ("not_ecommerce", "blocked", "inactive_storefront") or resolution == "nxdomain":
        return "negative"  # shop gone, domain gone, or now points at a non-public address
    if status == "temporarily_unreachable":
        return "transient"
    return "inconclusive"  # uncertain: bot wall, robots.txt, JS-only page …


def update_activity(db: Session, store: Store, status: str, confidence: float, resolution: str, now: datetime) -> None:
    before = store.activity_status
    first_check = store.last_successful_check_at is None and store.last_failed_check_at is None
    kind = failure_kind(status, resolution)
    if kind is None:
        store.consecutive_failures = 0
        store.last_successful_check_at = now
        store.activity_status = "active"
        store.activity_confidence = round(confidence, 2)
        store.activity_evidence = {"rule": "store signals verified", "check": status}
    else:
        store.consecutive_failures = (store.consecutive_failures or 0) + 1
        store.last_failed_check_at = now
        n = store.consecutive_failures
        last_ok = store.last_successful_check_at or store.first_verified_at or now
        days_since_ok = (now - _aware(last_ok)).days
        evidence = {"check": status, "resolution": resolution, "failure_kind": kind,
                    "consecutive_failures": n, "days_since_last_success": days_since_ok}  # fmt: skip
        if kind == "negative" and n >= settings.inactive_after_negative_checks:
            store.activity_status, store.activity_confidence = "inactive", 0.8
            evidence["rule"] = f"{n} consecutive checks found no store / no reachable domain"
        elif n >= settings.inactive_after_failed_checks and days_since_ok >= settings.inactive_min_days_since_success:
            store.activity_status, store.activity_confidence = "inactive", 0.5
            evidence["rule"] = f"{n} failed checks and no success for {days_since_ok} days"
        elif before in ("active", "recently_active"):
            store.activity_status, store.activity_confidence = "recently_active", 0.5
            evidence["rule"] = "worked recently; latest check did not confirm it (not treated as closed)"
        else:
            store.activity_status, store.activity_confidence = "uncertain", 0.2
            evidence["rule"] = "not enough successful evidence"
        store.activity_evidence = evidence
    if before != store.activity_status:
        if not first_check:
            store.last_changed_at = now
        db.add(
            StoreObservation(
                store_id=store.id,
                observation_type="activity_change",
                observation_data={"from": before, "to": store.activity_status, "evidence": store.activity_evidence},
                observed_at=now,
            )
        )
    store.next_check_at = next_check(store, kind, now)


def next_check(store: Store, kind: str | None, now: datetime) -> datetime:
    s = settings
    if store.activity_status == "inactive":
        days = s.recheck_inactive_days
    elif kind == "transient":
        days = s.retry_transient_days
    elif kind == "negative":
        days = s.retry_negative_days  # confirm quickly before calling it inactive
    elif kind == "inconclusive":
        days = s.retry_inconclusive_days  # bot walls don't go away by hammering them
    elif store.last_changed_at and now - _aware(store.last_changed_at) < timedelta(days=s.changed_window_days):
        days = s.recheck_changed_days
    elif store.first_verified_at and now - _aware(store.first_verified_at) < timedelta(days=s.new_store_window_days):
        days = s.recheck_new_store_days
    else:
        days = s.recheck_active_days
    return now + timedelta(days=days * random.uniform(0.9, 1.1))  # jitter spreads load


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)  # SQLite returns naive datetimes


# ---------- the scheduler tick ----------


def _in_flight_verify(domain_id_col):
    return exists().where(
        CrawlJob.domain_id == domain_id_col, CrawlJob.job_type == "verify", CrawlJob.status.in_(IN_FLIGHT)
    )


def _queue(db: Session, rows, priority: int, enqueue: Callable) -> int:
    jobs = [CrawlJob(domain_id=did, target_domain=host, job_type="verify", status="queued", priority=priority) for did, host in rows]
    db.add_all(jobs)
    db.commit()
    for job in jobs:
        try:
            enqueue(job.id)
        except Exception:  # broker down: the stuck-job sweep re-sends it later
            pass
    return len(jobs)


def recover_stuck(db: Session, now: datetime, enqueue_by_type: dict[str, Callable], enqueue_run: Callable) -> dict:
    """Messages can be lost (Redis restart, killed worker). Re-send jobs that made no progress for
    a long time; tasks are idempotent so a duplicate is harmless. Give up after repeated losses."""
    stale = now - timedelta(minutes=settings.stuck_after_minutes)
    jobs = db.scalars(
        select(CrawlJob).where(
            CrawlJob.status.in_(IN_FLIGHT),
            func.coalesce(CrawlJob.started_at, CrawlJob.scheduled_at) < stale,
            CrawlJob.scheduled_at < stale,
        ).limit(100)
    ).all()
    resent = abandoned = 0
    for job in jobs:
        enqueue = enqueue_by_type.get(job.job_type)
        if enqueue is None or job.attempts >= settings.max_job_attempts:
            job.status, job.completed_at = "failed", now
            job.error_message = (job.error_message or "") + " | abandoned: no progress after repeated attempts"
            abandoned += 1
            continue
        job.status, job.scheduled_at = "queued", now
        resent += 1
    db.commit()
    for job in jobs:
        if job.status == "queued":
            try:
                enqueue_by_type[job.job_type](job.id)
            except Exception:
                pass

    runs = 0
    for run in db.scalars(select(DiscoveryRun).where(DiscoveryRun.status.in_(IN_FLIGHT))):
        touched = (run.cursor or {}).get("touched")
        last = datetime.fromisoformat(touched) if touched else _aware(run.started_at or now)
        if last < stale:
            run.cursor = {**(run.cursor or {}), "touched": now.isoformat()}
            db.commit()
            try:
                enqueue_run(run.id, (run.cursor or {}).get("seq", 0))
                runs += 1
            except Exception:
                pass
    return {"jobs_resent": resent, "jobs_abandoned": abandoned, "runs_resumed": runs}


def tick(
    db: Session,
    now: datetime,
    enqueue_verify: Callable,
    enqueue_products: Callable,
    enqueue_run: Callable,
) -> dict:
    s = settings
    out = recover_stuck(db, now, {"verify": enqueue_verify, "products": enqueue_products}, enqueue_run)

    # 1. stores due for a re-check (verification; product re-check follows a positive result)
    due = db.execute(
        select(Store.domain_id, Domain.normalized_domain)
        .join(Domain, Domain.id == Store.domain_id)
        .where(or_(Store.next_check_at.is_(None), Store.next_check_at <= now), ~_in_flight_verify(Store.domain_id),
               Domain.suppressed_at.is_(None), Store.merged_into_store_id.is_(None))
        .order_by(Store.next_check_at.asc().nulls_first())
        .limit(s.monitor_rechecks_per_tick)
    ).all()
    out["store_rechecks"] = _queue(db, due, 4, enqueue_verify)

    # 2. never-verified candidates (oldest first). Adaptive: top the queue up to monitor_max_in_flight
    #    instead of a fixed quota — measured on the VPS, the fixed quota (not the worker) was the bottleneck.
    in_flight = db.scalar(select(func.count()).select_from(CrawlJob).where(CrawlJob.status.in_(IN_FLIGHT))) or 0
    budget = min(s.monitor_backlog_per_tick, max(0, s.monitor_max_in_flight - in_flight))
    any_verify = exists().where(CrawlJob.domain_id == Domain.id, CrawlJob.job_type == "verify")
    backlog = db.execute(
        select(Domain.id, Domain.normalized_domain)
        .where(Domain.verification_status == "pending", ~any_verify, Domain.suppressed_at.is_(None))
        .order_by(Domain.backlog_rank, Domain.first_seen_at)  # high-yield sources first (M10)
        .limit(budget)
    ).all()
    out["backlog_verifications"] = _queue(db, backlog, 7, enqueue_verify)

    # 3. stale inconclusive candidates (not stores — those are handled by 1.)
    is_store = exists().where(Store.domain_id == Domain.id)
    stale = db.execute(
        select(Domain.id, Domain.normalized_domain)
        .where(
            Domain.verification_status.in_(("uncertain", "temporarily_unreachable")),
            Domain.last_checked_at < now - timedelta(days=s.candidate_recheck_days),
            ~is_store,
            ~_in_flight_verify(Domain.id),
            Domain.suppressed_at.is_(None),
        )
        .order_by(Domain.last_checked_at)
        .limit(s.monitor_candidate_rechecks_per_tick)
    ).all()
    out["candidate_rechecks"] = _queue(db, stale, 8, enqueue_verify)

    # 4. discovery sources with a schedule
    from app.services import discovery  # local: discovery imports app.sources which imports fetcher

    started = []
    for src in db.scalars(select(DiscoverySource).where(DiscoverySource.enabled.is_(True))):
        hours = (src.configuration or {}).get("schedule_hours")
        if not hours:
            continue
        last = db.scalar(select(func.max(DiscoveryRun.started_at)).where(DiscoveryRun.source_id == src.id))
        if last is None or _aware(last) <= now - timedelta(hours=float(hours)):
            try:
                discovery.launch_run(db, src, enqueue_run)
                started.append(src.name)
            except discovery.RunConflict:
                pass  # already running
            except Exception:
                db.rollback()
    out["sources_started"] = started

    # 5. time-based signals (e.g. "newly verified" expires) need a periodic refresh
    from app.services import signals

    stale_signals = db.scalars(
        select(Store).where(or_(Store.signals_refreshed_at.is_(None), Store.signals_refreshed_at < now - timedelta(days=1)),
                            Store.merged_into_store_id.is_(None))
        .order_by(Store.signals_refreshed_at.asc().nulls_first()).limit(s.signals_refresh_per_tick)
    ).all()  # fmt: skip
    for store in stale_signals:
        signals.refresh_store(db, store, now)
    db.commit()
    out["signals_refreshed"] = len(stale_signals)

    from app.services import workspace

    out["alerts_created"] = workspace.run_alerts(db, now)
    out.update(compact_history(db, now))
    from app.services import metrics

    out["metric_snapshot_rows"] = metrics.take_daily_snapshot(db, now)
    return out


def compact_history(db: Session, now: datetime) -> dict:
    """Retention (M9): raw evidence of old *superseded* verification checks is reduced to its
    summary (status, confidence, score, platform). The latest check per domain, all
    observations and all product history are kept in full. Expired sessions are deleted."""
    from sqlalchemy import delete
    from sqlalchemy.orm import aliased

    from app.models import AuthSession

    cutoff = now - timedelta(days=settings.job_evidence_retention_days)
    newer = aliased(CrawlJob)
    jobs = db.scalars(
        select(CrawlJob)
        .where(
            CrawlJob.job_type == "verify",
            CrawlJob.status == "succeeded",
            CrawlJob.completed_at < cutoff,
            exists().where(newer.domain_id == CrawlJob.domain_id, newer.job_type == "verify",
                           newer.status == "succeeded", newer.completed_at > CrawlJob.completed_at),
        )
        .limit(settings.compact_per_tick * 4)
    ).all()  # fmt: skip
    compacted = 0
    for job in jobs:
        r = job.result or {}
        if (r.get("evidence") or {}).get("compacted"):
            continue
        pf = r.get("platform") or {}
        job.result = {
            "status": r.get("status"), "confidence": r.get("confidence"), "resolution": r.get("resolution"),
            "platform": {"platform": pf.get("platform"), "confidence": pf.get("confidence")} if pf else None,
            "evidence": {"compacted": True, "compacted_at": now.isoformat(), "score": (r.get("evidence") or {}).get("score")},
        }  # fmt: skip
        compacted += 1
        if compacted >= settings.compact_per_tick:
            break
    sessions = db.execute(delete(AuthSession).where(AuthSession.expires_at < now)).rowcount or 0
    db.commit()
    return {"evidence_compacted": compacted, "sessions_expired": sessions}


def status(db: Session, now: datetime) -> dict:
    def count(q) -> int:
        return db.scalar(q) or 0

    any_verify = exists().where(CrawlJob.domain_id == Domain.id, CrawlJob.job_type == "verify")
    return {
        "stores_due": count(select(func.count()).select_from(Store).where(or_(Store.next_check_at.is_(None), Store.next_check_at <= now))),
        "unverified_backlog": count(select(func.count()).select_from(Domain).where(Domain.verification_status == "pending", ~any_verify)),
        "jobs_in_flight": count(select(func.count()).select_from(CrawlJob).where(CrawlJob.status.in_(IN_FLIGHT))),
        "activity": dict(db.execute(select(Store.activity_status, func.count()).group_by(Store.activity_status)).tuples().all()),
        "next_store_check": db.scalar(select(func.min(Store.next_check_at)).where(Store.next_check_at > now)),
        "tick_seconds": settings.monitor_tick_seconds,
        "per_tick": {"rechecks": settings.monitor_rechecks_per_tick, "backlog": settings.monitor_backlog_per_tick,
                     "candidate_rechecks": settings.monitor_candidate_rechecks_per_tick},  # fmt: skip
    }
