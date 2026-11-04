"""Shared ingestion for every discovery source: dedupe URLs per source, dedupe domains
globally, queue verification for domains never seen before. Never writes `stores`."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import CrawlJob, DiscoveredUrl, DiscoveryRun, DiscoverySource, Domain
from app.services import suppression
from app.services.domains import InvalidDomain, normalize_domain
from app.sources import Found

MAX_URL = 2000  # keeps the (source_id, normalized_url) btree entry well under Postgres' limit
RUN_STATUSES = ("queued", "running", "retrying", "succeeded", "failed", "cancelled")
ACTIVE_RUN = ("queued", "running", "retrying")


def normalize_url(raw: str) -> tuple[str, str] | None:
    """→ (normalized_url, normalized_domain), or None if unusable."""
    raw = raw.strip()
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        host = normalize_domain(parts.hostname or "")
    except (InvalidDomain, ValueError):
        return None
    if parts.scheme not in ("http", "https"):
        return None
    url = urlunsplit(("https" if parts.scheme == "https" else "http", host, parts.path or "/", parts.query, ""))
    return url[:MAX_URL], host


def _chunks(items: list, size: int = 500):
    # stays under SQLite's / Postgres' bound-parameter limits for multi-row VALUES
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _insert(db: Session, model):
    return (pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert)(model)


def ingest(
    db: Session, source_id: uuid.UUID, run: DiscoveryRun, found: list[Found], verify_budget: int, enqueue: Callable
) -> dict[str, int]:
    """Idempotent: re-ingesting the same batch changes only last_discovered_at."""
    now = datetime.now(UTC)
    urls: dict[str, tuple[str, datetime]] = {}
    refs: dict[str, str] = {}
    invalid = 0
    for f in found:
        n = normalize_url(f.url)
        if n is None:
            invalid += 1
            continue
        urls.setdefault(n[0], (n[1], f.seen_at or now))
        if f.ref:
            refs.setdefault(n[0], f.ref[:200])
    roots = suppression.suppressed_roots(db)
    dropped = 0
    if roots:  # opt-outs are excluded before anything is recorded (incl. subdomains)
        keep = {u: v for u, v in urls.items() if not suppression.matches(v[0], roots)}
        dropped, urls = len(urls) - len(keep), keep
    if not urls:
        return {"urls": 0, "invalid": invalid, "new_domains": 0, "queued": 0, "suppressed": dropped}

    source = db.get(DiscoverySource, source_id)
    config = (source.configuration or {}) if source else {}
    rank = int(config.get("backlog_rank", 5))
    if config.get("evidence_only"):
        # Supporting-evidence source (crt.sh since M10): attach records to domains we already know,
        # never create candidates or queue verification from it.
        known = set(db.scalars(select(Domain.normalized_domain).where(Domain.normalized_domain.in_(sorted({h for h, _ in urls.values()})))))
        urls = {u: v for u, v in urls.items() if v[0] in known}
        verify_budget = 0
        if not urls:
            return {"urls": 0, "invalid": invalid, "new_domains": 0, "queued": 0, "suppressed": dropped}
    hosts = sorted({h for h, _ in urls.values()})
    first_seen = {}
    for h, seen in urls.values():
        first_seen[h] = min(first_seen.get(h, seen), seen)

    # Domains: insert the unseen ones; RETURNING tells us exactly which were new (race-safe).
    rows = [
        {"id": uuid.uuid4(), "normalized_domain": h, "first_seen_at": first_seen[h], "last_seen_at": now,
         "created_at": now, "updated_at": now, "verification_status": "pending", "backlog_rank": rank}
        for h in hosts
    ]  # fmt: skip
    new: dict[str, uuid.UUID] = {}
    ids: dict[str, uuid.UUID] = {}
    for chunk in _chunks(rows):
        stmt = _insert(db, Domain).values(chunk).on_conflict_do_nothing(index_elements=["normalized_domain"])
        new.update(db.execute(stmt.returning(Domain.normalized_domain, Domain.id)).tuples().all())
    for chunk in _chunks(hosts):
        # a better-ranked source re-finding a known pending candidate moves it forward
        db.execute(update(Domain).where(Domain.normalized_domain.in_(chunk), Domain.backlog_rank > rank).values(backlog_rank=rank))
        ids.update(db.execute(select(Domain.normalized_domain, Domain.id).where(Domain.normalized_domain.in_(chunk))).tuples().all())

    url_rows = (
        [
            {"id": uuid.uuid4(), "normalized_url": u, "hostname": h, "source_id": source_id,
             "discovery_run_id": run.id, "domain_id": ids[h], "first_discovered_at": seen,
             "last_discovered_at": now, "processing_status": "pending", "source_ref": refs.get(u)}
            for u, (h, seen) in urls.items()
        ]  # fmt: skip
    )
    for chunk in _chunks(url_rows):
        stmt = _insert(db, DiscoveredUrl).values(chunk)
        earlier = stmt.excluded.first_discovered_at < DiscoveredUrl.first_discovered_at
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=["source_id", "normalized_url"],
                set_={
                    "last_discovered_at": now,
                    "discovery_run_id": run.id,
                    "source_ref": stmt.excluded.source_ref,
                    # a source may later report an older sighting (e.g. full CT history): keep the earliest
                    "first_discovered_at": case((earlier, stmt.excluded.first_discovered_at), else_=DiscoveredUrl.first_discovered_at),
                },
            )
        )

    # Only brand-new domains get a verification job here; known ones are re-checked by monitoring (M6).
    jobs = [
        CrawlJob(domain_id=did, target_domain=h, job_type="verify", status="queued", priority=7)
        for h, did in sorted(new.items())[: max(verify_budget, 0)]
    ]
    db.add_all(jobs)
    db.commit()
    for job in jobs:
        try:
            enqueue(job.id)
        except Exception as e:  # the domain is stored; a later backlog sweep can re-queue it
            job.status = "failed"
            job.error_message = f"queue unavailable: {type(e).__name__}"
    db.commit()
    return {"urls": len(urls), "invalid": invalid, "new_domains": len(new), "queued": len(jobs), "suppressed": dropped}


def start_run(db: Session, source: DiscoverySource) -> DiscoveryRun | None:
    """Create a queued run, or None if one is already active for this source."""
    active = db.scalar(
        select(DiscoveryRun.id).where(DiscoveryRun.source_id == source.id, DiscoveryRun.status.in_(ACTIVE_RUN))
    )
    if active:
        return None
    run = DiscoveryRun(source_id=source.id, status="queued", cursor={"seq": 0}, started_at=datetime.now(UTC))
    db.add(run)
    db.commit()
    return run


class RunConflict(Exception):
    pass


def _active(db: Session, source_id: uuid.UUID) -> bool:
    q = select(DiscoveryRun.id).where(DiscoveryRun.source_id == source_id, DiscoveryRun.status.in_(ACTIVE_RUN))
    return db.scalar(q) is not None


def launch_run(db: Session, source: DiscoverySource, enqueue: Callable) -> DiscoveryRun:
    if source.restricted:
        raise RunConflict(f"source is restricted: {source.restriction_reason or 'usage rights not cleared'}")
    if not source.enabled:
        raise RunConflict("source is disabled")
    run = start_run(db, source)
    if run is None:
        raise RunConflict("a run is already active for this source")
    _kick(db, run, enqueue)
    return run


def resume_run(db: Session, run: DiscoveryRun, enqueue: Callable) -> DiscoveryRun:
    """Continue a failed/cancelled run from its stored cursor."""
    if run.status not in ("failed", "cancelled") or (run.cursor or {}).get("done"):
        raise RunConflict(f"run is {run.status}; only unfinished failed/cancelled runs can resume")
    if _active(db, run.source_id):
        raise RunConflict("another run is active for this source")
    run.status, run.completed_at, run.error_summary = "queued", None, None
    db.commit()
    _kick(db, run, enqueue)
    return run


def _kick(db: Session, run: DiscoveryRun, enqueue: Callable) -> None:
    try:
        enqueue(run.id, (run.cursor or {}).get("seq", 0))
    except Exception as e:
        run.status, run.error_summary = "failed", f"queue unavailable: {type(e).__name__}"
        db.commit()
        raise


def list_sources(db: Session) -> list[tuple[DiscoverySource, DiscoveryRun | None, int]]:
    out = []
    for s in db.scalars(select(DiscoverySource).order_by(DiscoverySource.name)):
        last = db.scalar(
            select(DiscoveryRun).where(DiscoveryRun.source_id == s.id).order_by(DiscoveryRun.started_at.desc()).limit(1)
        )
        urls = db.scalar(select(func.count()).select_from(DiscoveredUrl).where(DiscoveredUrl.source_id == s.id))
        out.append((s, last, urls or 0))
    return out


def list_runs(db: Session, source_id: uuid.UUID | None, limit: int) -> list[DiscoveryRun]:
    q = select(DiscoveryRun).order_by(DiscoveryRun.started_at.desc()).limit(limit)
    if source_id:
        q = q.where(DiscoveryRun.source_id == source_id)
    return list(db.scalars(q))
