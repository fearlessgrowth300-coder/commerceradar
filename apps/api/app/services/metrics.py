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
        .where(CrawlJob.completed_at >= since, CrawlJob.completed_at < now)
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
        .where(DiscoverySource.enabled.is_(True))
        .join(DiscoveredUrl, DiscoveredUrl.source_id == DiscoverySource.id)
        .join(Domain, Domain.id == DiscoveredUrl.domain_id)
        .group_by(DiscoverySource.name, Domain.verification_status)
    )
    roles = dict(db.execute(select(DiscoverySource.name, DiscoverySource.configuration)).tuples().all())
    by: dict[str, dict[str, int]] = {}
    for name, status, n in db.execute(q).all():
        by.setdefault(name, {})[status] = n
    out = []
    for name, counts in sorted(by.items()):
        total = sum(counts.values())
        checked = total - counts.get("pending", 0)
        stores = sum(counts.get(s, 0) for s in STORE_STATUSES)
        role = "evidence" if (roles.get(name) or {}).get("evidence_only") else "discovery"
        out.append({"source": name, "role": role, "candidates": total, "verified": checked, "stores": stores,
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
        "unique_contribution": unique_contribution(db, now),
        "processing_cost_7d": {k: v for k, v in processing_cost(db, now).items() if k != "per_source"},
        "storage": storage(db),
        "queues": queues(),
    }
    if settings.vps_monthly_cost_usd and stores_24h:
        out["verification"]["usd_per_new_store_estimate"] = round(settings.vps_monthly_cost_usd / (stores_24h * 30), 4)
    return out



# ---------- unique-store contribution (M12-A) ----------


def unique_contribution(db: Session, now: datetime | None = None, days: int = 7) -> list[dict]:
    """Per discovery source, counted on LIVE STORES (not candidate domains), alias-aware:
    a store is attributed to every source that found any of its current domains (primary or alias).
      unique_stores  found by this source and no other source
      shared_stores  also found by at least one other source
      verify_seconds_7d / seconds_per_new_store  worker time spent verifying this source's candidates
    Merged, suppressed and manually-submitted-only stores are excluded from per-source counts."""
    from app.models import StoreDomain

    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    pairs = db.execute(
        select(DiscoverySource.name, Store.id)
        .join(DiscoveredUrl, DiscoveredUrl.source_id == DiscoverySource.id)
        .join(StoreDomain, (StoreDomain.domain_id == DiscoveredUrl.domain_id) & StoreDomain.valid_to.is_(None)
              & StoreDomain.role.in_(("primary", "alias")))
        .join(Store, Store.id == StoreDomain.store_id)
        .join(Domain, Domain.id == Store.domain_id)
        .where(Store.merged_into_store_id.is_(None), Domain.suppressed_at.is_(None))
        .distinct()
    ).all()  # fmt: skip
    by_store: dict = {}
    for src, sid in pairs:
        by_store.setdefault(sid, set()).add(src)
    new_ids = set(db.scalars(select(Store.id).where(Store.first_verified_at >= since, Store.merged_into_store_id.is_(None))))

    sources = db.execute(select(DiscoverySource.name, DiscoverySource.id, DiscoverySource.restricted, DiscoverySource.enabled)).all()
    attributed = processing_cost(db, now, days)["per_source"]
    out = []
    for name, sid, restricted, enabled in sorted(sources):
        candidates = db.scalar(select(func.count(func.distinct(DiscoveredUrl.domain_id))).where(DiscoveredUrl.source_id == sid)) or 0
        processed = db.scalar(
            select(func.count(func.distinct(Domain.id))).join(DiscoveredUrl, DiscoveredUrl.domain_id == Domain.id)
            .where(DiscoveredUrl.source_id == sid, Domain.verification_status != "pending")
        ) or 0  # fmt: skip
        mine = [st for st, srcs in by_store.items() if name in srcs]
        unique = [st for st in mine if by_store[st] == {name}]
        new_here = len([st for st in mine if st in new_ids])
        new_unique = len([st for st in unique if st in new_ids])
        att = attributed.get(name, {"verify": 0.0, "products": 0.0, "verify_jobs": 0.0})
        total_att = att["verify"] + att["products"]
        out.append({
            "source": name, "restricted": bool(restricted), "enabled": bool(enabled),
            "candidates": candidates, "processed": processed, "stores": len(mine),
            "unique_stores": len(unique), "shared_stores": len(mine) - len(unique),
            "store_rate": round(len(mine) / processed, 3) if processed else None,
            "new_stores_7d": new_here, "new_unique_stores_7d": new_unique,
            # actual worker time, split across sources that share a domain (sums to the real total)
            "attributed_verify_seconds_7d": round(att["verify"]),
            "attributed_product_seconds_7d": round(att["products"]),
            "attributed_seconds_7d": round(total_att),
            "attributed_verify_jobs_7d": round(att["verify_jobs"], 1),
            # incremental cost of what ONLY this source contributes
            "seconds_per_new_unique_store": round(total_att / new_unique) if new_unique else None,
        })  # fmt: skip
    return out


def processing_cost(db: Session, now: datetime | None = None, days: int = 7) -> dict:
    """Actual worker time in the window, each job counted ONCE, then split equally across the
    sources that found the job's domain (no double counting; shares sum to the total).
    Jobs whose domain has no source record (manual submissions, re-checks of manually added
    stores) are reported as `unattributed`."""
    now = now or datetime.now(UTC)
    since = now - timedelta(days=days)
    jobs = db.execute(
        select(CrawlJob.job_type, CrawlJob.domain_id, CrawlJob.started_at, CrawlJob.completed_at)
        .where(CrawlJob.status == "succeeded", CrawlJob.completed_at >= since, CrawlJob.completed_at < now, CrawlJob.job_type.in_(("verify", "products")))
    ).all()  # fmt: skip
    dom_ids = {d for _, d, _, _ in jobs if d}
    found_by: dict = {}
    for chunk_start in range(0, len(dom_ids), 1000):
        chunk = list(dom_ids)[chunk_start : chunk_start + 1000]
        for dom, src in db.execute(
            select(DiscoveredUrl.domain_id, DiscoverySource.name)
            .join(DiscoverySource, DiscoverySource.id == DiscoveredUrl.source_id)
            .where(DiscoveredUrl.domain_id.in_(chunk)).distinct()
        ).all():  # fmt: skip
            found_by.setdefault(dom, set()).add(src)
    per: dict = {}
    total = {"verify": 0.0, "products": 0.0}
    unattributed = {"verify": 0.0, "products": 0.0}
    counts = {"verify": 0, "products": 0}
    for jtype, dom, start, end in jobs:
        if not (start and end):
            continue
        secs = max((end - start).total_seconds(), 0.0)
        total[jtype] += secs
        counts[jtype] += 1
        srcs = found_by.get(dom)
        if not srcs:
            unattributed[jtype] += secs
            continue
        share = secs / len(srcs)
        for s in srcs:
            row = per.setdefault(s, {"verify": 0.0, "products": 0.0, "verify_jobs": 0.0})
            row[jtype] += share
            if jtype == "verify":
                row["verify_jobs"] += 1 / len(srcs)
    return {"window_days": days, "jobs": counts, "actual_seconds": {k: round(v) for k, v in total.items()},
            "unattributed_seconds": {k: round(v) for k, v in unattributed.items()}, "per_source": per}  # fmt: skip


def take_daily_snapshot(db: Session, now: datetime | None = None) -> int:
    """Idempotent: at most one value per (day, metric, source)."""
    from app.models import MetricSnapshot

    now = now or datetime.now(UTC)
    day = now.strftime("%Y-%m-%d")
    if db.scalar(select(func.count()).select_from(MetricSnapshot).where(MetricSnapshot.taken_on == day)):
        return 0
    rows = [
        ("live_stores", "", db.scalar(select(func.count()).select_from(Store).where(Store.merged_into_store_id.is_(None))) or 0),
        ("candidate_domains", "", db.scalar(select(func.count()).select_from(Domain)) or 0),
        ("pending_backlog", "", db.scalar(select(func.count()).select_from(Domain).where(Domain.verification_status == "pending")) or 0),
    ]
    for r in unique_contribution(db, now):
        for k in ("stores", "unique_stores", "shared_stores", "processed", "candidates"):
            rows.append((f"source_{k}", r["source"], r[k]))
    db.add_all(MetricSnapshot(taken_on=day, metric=m, source=src, value=float(v)) for m, src, v in rows)
    db.commit()
    return len(rows)


def history(db: Session, metric: str, days: int = 90) -> list[dict]:
    from app.models import MetricSnapshot

    q = select(MetricSnapshot).where(MetricSnapshot.metric == metric).order_by(MetricSnapshot.taken_on.desc(), MetricSnapshot.source)
    return [{"day": m.taken_on, "source": m.source, "value": m.value} for m in db.scalars(q.limit(days * 20))]
