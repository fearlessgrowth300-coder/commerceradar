import ipaddress
import re
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CrawlJob, Domain, Store

# Names that only exist on private networks; DNS checks would block them anyway, this fails fast.
RESERVED_TLDS = {"localhost", "local", "internal", "lan", "home", "corp", "arpa", "test", "invalid", "onion", "intranet"}
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
IN_FLIGHT = ("queued", "running", "retrying")
VERIFICATION_STATUSES = (
    "pending",
    "confirmed_ecommerce",
    "probable_ecommerce",
    "not_ecommerce",
    "uncertain",
    "temporarily_unreachable",
    "blocked",
)


class InvalidDomain(ValueError):
    pass


class QueueUnavailable(Exception):
    pass


def _is_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def normalize_domain(raw: str) -> str:
    """'https://WWW.Example.COM:443/path' -> 'example.com'. Raises InvalidDomain.

    Keeps subdomains (shop.example.com is a different site from example.com), strips only 'www.'.
    """
    s = (raw or "").strip().lower()
    if not s or len(s) > 2000 or any(c.isspace() for c in s):
        raise InvalidDomain("enter a domain like example.com")
    if "://" not in s:
        s = "http://" + s
    try:
        host = urlsplit(s).hostname
    except ValueError as e:
        raise InvalidDomain("not a valid URL or domain") from e
    if not host:
        raise InvalidDomain("no hostname found")
    host = host.rstrip(".")
    if _is_ip(host.strip("[]")):
        raise InvalidDomain("IP addresses are not accepted; submit a domain name")
    if host.startswith("www."):
        host = host[4:]
    try:
        host = host.encode("idna").decode("ascii")  # ponytail: IDNA 2003 (stdlib); switch to `idna` pkg if 2008-only names matter
    except UnicodeError as e:
        raise InvalidDomain("invalid internationalized domain") from e
    labels = host.split(".")
    if len(host) > 253 or len(labels) < 2 or not all(_LABEL.match(label) for label in labels):
        raise InvalidDomain("not a valid domain name")
    if labels[-1] in RESERVED_TLDS or labels[-1].isdigit():
        raise InvalidDomain("private or reserved domain names are not accepted")
    return host


def submit_domain(db: Session, raw: str, enqueue) -> tuple[Domain, CrawlJob, bool]:
    """Create (or reuse) the candidate domain and its verification job.

    Returns (domain, job, created). Idempotent: an in-flight job for the same domain is reused.
    `enqueue(job_id)` puts the job on the queue; injected so tests don't need Redis.
    """
    host = normalize_domain(raw)
    now = datetime.now(UTC)
    domain = db.scalar(select(Domain).where(Domain.normalized_domain == host))
    from app.services import suppression

    if (domain is not None and domain.suppressed_at is not None) or suppression.is_suppressed(db, host):
        raise InvalidDomain("this domain has opted out and is not crawled")
    created = domain is None
    if created:
        domain = Domain(normalized_domain=host)
        db.add(domain)
        try:
            db.flush()
        except IntegrityError:  # concurrent submit of the same domain
            db.rollback()
            domain = db.scalars(select(Domain).where(Domain.normalized_domain == host)).one()
            created = False
    else:
        domain.last_seen_at = now

    job = db.scalar(
        select(CrawlJob)
        .where(CrawlJob.domain_id == domain.id, CrawlJob.job_type == "verify", CrawlJob.status.in_(IN_FLIGHT))
        .order_by(CrawlJob.scheduled_at.desc())
        .limit(1)
    )
    if job:
        db.commit()
        return domain, job, created

    job = CrawlJob(domain_id=domain.id, target_domain=host, job_type="verify", status="queued")
    db.add(job)
    db.commit()
    try:
        enqueue(job.id)
    except Exception as e:
        job.status = "failed"
        job.error_message = f"queue unavailable: {type(e).__name__}"
        db.commit()
        raise QueueUnavailable(job.error_message) from e
    return domain, job, created


def list_domains(db: Session, status: str | None, q: str | None, page: int, page_size: int):
    stmt = select(Domain).where(Domain.suppressed_at.is_(None))  # opt-outs: admin list only
    if status:
        stmt = stmt.where(Domain.verification_status == status)
    if q:
        stmt = stmt.where(Domain.normalized_domain.contains(q.strip().lower(), autoescape=True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(Domain.first_seen_at.desc(), Domain.id).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return items, total


def get_domain_detail(db: Session, domain_id: uuid.UUID):
    domain = db.get(Domain, domain_id)
    if not domain or domain.suppressed_at is not None:
        return None
    jobs = db.scalars(
        select(CrawlJob).where(CrawlJob.domain_id == domain_id).order_by(CrawlJob.scheduled_at.desc()).limit(20)
    ).all()
    store = db.scalar(select(Store).where(Store.domain_id == domain_id))
    return domain, jobs, store
