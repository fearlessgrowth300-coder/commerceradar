"""Opt-out / suppression (spec §16), hardened in M10.

A suppressed domain also covers every subdomain (`optedout.com` ⇒ `shop.optedout.com`).
Enforced at every stage:
  ingestion   – discovery.ingest drops matching URLs (nothing recorded, no candidate created)
  submission  – manual submit refuses; monitoring only queues non-suppressed rows
  crawling    – SafeFetcher refuses suppressed hosts on EVERY hop, so a redirect from an
                unsuppressed domain into a suppressed one is never followed
  extraction  – same fetcher guard; task also checks the job's domain
  views       – hidden from all ordinary listings; visible only on the admin suppression list
Every suppress / unsuppress is appended to suppression_events (who, when, why).
"""

from datetime import UTC, datetime

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.models import CrawlJob, Domain, SuppressionEvent

COVERED = "covered by opt-out of "


def parents(host: str) -> list[str]:
    """`a.b.example.com` → [a.b.example.com, b.example.com, example.com] (never a bare TLD)."""
    labels = host.lower().strip(".").split(".")
    return [".".join(labels[i:]) for i in range(len(labels) - 1)] or [host]


def suppressed_roots(db: Session) -> frozenset[str]:
    """Explicitly suppressed domains (small set). Loaded once per task for the fetcher guard."""
    q = select(Domain.normalized_domain).where(Domain.suppressed_at.is_not(None))
    return frozenset(db.scalars(q))


def matches(host: str, roots: frozenset[str] | set[str]) -> bool:
    host = (host or "").lower().removeprefix("www.")
    return any(p in roots for p in parents(host))


def is_suppressed(db: Session, host: str) -> bool:
    q = select(Domain.id).where(Domain.normalized_domain.in_(parents(host)), Domain.suppressed_at.is_not(None)).limit(1)
    return db.scalar(q) is not None


def guard(db: Session):
    """Callable for SafeFetcher(blocked=...)."""
    roots = suppressed_roots(db)
    return (lambda host: matches(host, roots)) if roots else None


def suppress(db: Session, host: str, reason: str, actor_id=None) -> Domain:
    now = datetime.now(UTC)
    reason = reason.strip()[:300] or "opt-out"
    d = db.scalar(select(Domain).where(Domain.normalized_domain == host))
    if d is None:
        d = Domain(normalized_domain=host)  # pre-emptive opt-out: never crawl it
        db.add(d)
        db.flush()
    d.suppressed_at, d.suppression_reason = now, reason
    # Existing subdomain candidates are covered too (marked, so un-suppressing can undo exactly this).
    subs = select(Domain.id).where(Domain.normalized_domain.like(f"%.{host}"), Domain.suppressed_at.is_(None))
    sub_ids = list(db.scalars(subs))
    if sub_ids:
        db.execute(update(Domain).where(Domain.id.in_(sub_ids)).values(suppressed_at=now, suppression_reason=COVERED + host))
    db.execute(
        update(CrawlJob)
        .where(CrawlJob.domain_id.in_([d.id, *sub_ids]), CrawlJob.status.in_(("queued", "retrying")))
        .values(status="failed", error_message="domain suppressed (opt-out)")
    )
    db.add(SuppressionEvent(domain=host, action="suppress", reason=reason, actor_user_id=actor_id, subdomains=len(sub_ids)))
    db.commit()
    return d


def unsuppress(db: Session, host: str, actor_id=None) -> Domain | None:
    d = db.scalar(select(Domain).where(Domain.normalized_domain == host, Domain.suppressed_at.is_not(None)))
    if d is None:
        return None
    d.suppressed_at, d.suppression_reason = None, None
    n = db.execute(
        update(Domain)
        .where(Domain.suppression_reason == COVERED + host)
        .values(suppressed_at=None, suppression_reason=None)
    ).rowcount
    db.add(SuppressionEvent(domain=host, action="unsuppress", reason=None, actor_user_id=actor_id, subdomains=n or 0))
    db.commit()
    return d


def listed(db: Session) -> list[Domain]:
    """Explicit opt-outs (not the rows covered through a parent)."""
    q = select(Domain).where(
        Domain.suppressed_at.is_not(None),
        or_(Domain.suppression_reason.is_(None), ~Domain.suppression_reason.startswith(COVERED)),
    )
    return list(db.scalars(q.order_by(Domain.suppressed_at.desc())))


def events(db: Session, limit: int = 200) -> list[SuppressionEvent]:
    return list(db.scalars(select(SuppressionEvent).order_by(SuppressionEvent.created_at.desc()).limit(limit)))
