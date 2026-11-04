"""Measured operating metrics (M9): what the system actually did, from its own records.

Everything here is computed from crawl_jobs / domains / discovered_urls timestamps and
database statistics. The only estimate is the optional $/store figure, which needs
VPS_MONTHLY_COST_USD and is labelled as an estimate.
"""

import statistics
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import CrawlJob, DiscoveredUrl, DiscoverySource, Domain, Store

STORE_STATUSES = ("confirmed_ecommerce", "probable_ecommerce")


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 1)
    return round(statistics.quantiles(values, n=100, method="inclusive")[int(q * 100) - 1], 1)


def job_stats(db: Session, now: datetime, hours: int = 24) -> dict:
    since = now - timedelta(hours=hours)
    rows = db.execute(
        select(CrawlJob.job_type, CrawlJob.status, CrawlJob.started_at, CrawlJob.completed_at, CrawlJob.attempts)
        .where(CrawlJob.completed_at >= since)
    ).all()
    out: dict[str, dict] = {}
    # Rate over the time actually observed: a system that started 3 h ago must not be averaged over 24 h.
    first = min((r.completed_at for r in rows), default=None)
    if first is not None and first.tzinfo is None:
        first = first.replace(tzinfo=UTC)
    span_h = max((now - first).total_seconds() / 3600, 0.25) if first else hours
    span_h = min(span_h, hours)
    for jt in sorted({r.job_type for r in rows}):
        mine = [r for r in rows if r.job_type == jt]
        durs = [(r.completed_at - r.started_at).total_seconds() for r in mine if r.started_at and r.status == "succeeded"]
        out[jt] = {
            "completed": len(mine),
            "succeeded": sum(r.status == "succeeded" for r in mine),
            "failed": sum(r.status == "failed" for r in mine),
            "per_hour": round(len(mine) / span_h, 1),
            "observed_hours": round(span_h, 2),
            "avg_seconds": round(statistics.fmean(durs), 1) if durs else None,
            "p50_seconds": _pct(durs, 0.5),
            "p95_seconds": _pct(durs, 0.95),
            "retried": sum((r.attempts or 0) > 1 for r in mine),
        }
    return out


def source_yield(db: Session) -> list[dict]:
    """Per source: candidates found, how many were verified so far, how many became stores."""
    q = (
        select(DiscoverySource.name, Domain.verification_status, func.count(func.distinct(Domain.id)))
        .join(DiscoveredUrl, DiscoveredUrl.source_id == DiscoverySource.id)
        .join(Domain, Domain.id == DiscoveredUrl.domain_id)
        .group_by(DiscoverySource.name, Domain.verification_status)
    )
    by: dict[str, dict[str, int]] = {}
    for name, status, n in db.execute(q).all():
        by.setdefault(name, {})[status] = n
    out = []
    for name, counts in sorted(by.items()):
        total = sum(counts.values())
        checked = total - counts.get("pending", 0)
        stores = sum(counts.get(s, 0) for s in STORE_STATUSES)
        out.append({"source": name, "candidates": total, "verified": checked, "stores": stores,
                    "store_rate": round(stores / checked, 3) if checked else None})  # fmt: skip
    return out


def storage(db: Session) -> dict:
    if db.get_bind().dialect.name != "postgresql":
        return {"note": "table sizes are only reported on PostgreSQL"}
    tables = db.execute(text(
        "select relname, n_live_tup, pg_total_relation_size(relid) from pg_stat_user_tables order by 3 desc limit 15"
    )).all()  # fmt: skip
    domains = db.scalar(select(func.count()).select_from(Domain)) or 0
    size = db.scalar(text("select pg_database_size(current_database())")) or 0
    return {
        "database_bytes": size,
        "bytes_per_domain": round(size / domains) if domains else None,
        "tables": [{"table": t, "rows": n, "bytes": b} for t, n, b in tables],
    }


def queues() -> dict:
    try:
        import redis

        r = redis.from_url(settings.redis_url, socket_timeout=2)
        return {q: r.llen(q) for q in ("celery", "verification", "discovery", "products")}
    except Exception as e:  # metrics must never break the admin page
        return {"error": type(e).__name__}


def snapshot(db: Session, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    jobs = job_stats(db, now)
    verify = jobs.get("verify", {})
    pending = db.scalar(select(func.count()).select_from(Domain).where(Domain.verification_status == "pending")) or 0
    stores_24h = db.scalar(select(func.count()).select_from(Store).where(Store.first_verified_at >= now - timedelta(hours=24))) or 0
    worker_seconds = (verify.get("avg_seconds") or 0) * (verify.get("succeeded") or 0)
    capacity = (settings.worker_concurrency * 3600 / verify["avg_seconds"]) if verify.get("avg_seconds") else None
    rate = verify.get("per_hour") or 0
    out = {
        "measured_at": now,
        "window_hours": 24,
        "jobs": jobs,
        "verification": {
            "pending_backlog": pending,
            "measured_per_hour": rate,
            "worker_capacity_per_hour_estimate": round(capacity) if capacity else None,
            "backlog_eta_hours": round(pending / rate, 1) if rate else None,
            "new_stores_24h": stores_24h,
            "worker_seconds_per_new_store": round(worker_seconds / stores_24h) if stores_24h else None,
        },
        "source_yield": source_yield(db),
        "storage": storage(db),
        "queues": queues(),
    }
    if settings.vps_monthly_cost_usd and stores_24h:
        out["verification"]["usd_per_new_store_estimate"] = round(settings.vps_monthly_cost_usd / (stores_24h * 30), 4)
    return out
