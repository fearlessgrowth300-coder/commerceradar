"""Tenant features (M8): saved searches, alerts, CSV exports; admin: suppression and overview.

Saved-search filters are exactly the store-directory filters, validated against the same
whitelist, so an alert matches what the user saw in the directory.
"""

import csv
import io
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import (Alert, AuthSession, CrawlJob, Domain, ExportedStore, Membership, Organization, SavedSearch, Store, User,
                        UsageCounter)  # fmt: skip
from app.services import billing, stores

FILTER_KEYS = ("q", "platform", "activity", "country", "ecommerce_status", "signal", "verified_from", "verified_to", "sort")
EXPORT_COLUMNS = ("domain", "store_name", "primary_platform", "platform_confidence", "ecommerce_status", "activity_status",
                  "business_country", "first_verified_at", "last_verified_at", "products_observed")  # fmt: skip


class NotFound(Exception):
    pass


def clean_filters(raw: dict) -> dict:
    out = {k: str(v)[:253] for k, v in (raw or {}).items() if k in FILTER_KEYS and v not in (None, "")}
    for k in ("verified_from", "verified_to"):
        if k in out:
            out[k] = datetime.fromisoformat(out[k]).isoformat()  # ValueError → 422 upstream
    return out


def _query_kwargs(filters: dict) -> dict:
    kw = {k: v for k, v in filters.items() if k not in ("verified_from", "verified_to")}
    for k in ("verified_from", "verified_to"):
        if k in filters:
            dt = datetime.fromisoformat(filters[k])
            kw[k] = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return kw


# ---------- saved searches + alerts ----------


def create_search(db: Session, org: Organization, user: User, name: str, filters: dict, alerts: bool) -> SavedSearch:
    billing.check_saved_search(db, org, alerts)
    s = SavedSearch(organization_id=org.id, user_id=user.id, name=name.strip()[:200] or "Saved search",
                    filters=clean_filters(filters), alerts_enabled=alerts,
                    last_alert_check_at=datetime.now(UTC))  # alerts cover stores verified from now on  # fmt: skip
    db.add(s)
    db.commit()
    return s


def get_search(db: Session, org: Organization, search_id: uuid.UUID) -> SavedSearch:
    s = db.get(SavedSearch, search_id)
    if s is None or s.organization_id != org.id:  # tenant isolation: other orgs' rows look nonexistent
        raise NotFound()
    return s


def delete_search(db: Session, org: Organization, search_id: uuid.UUID) -> None:
    s = get_search(db, org, search_id)
    db.query(Alert).filter(Alert.saved_search_id == s.id).delete()
    db.delete(s)
    db.commit()


def run_alerts(db: Session, now: datetime, limit_per_search: int = 200) -> int:
    """Create alerts for stores first verified since each search's last check. Idempotent."""
    created = 0
    for s in db.scalars(select(SavedSearch).where(SavedSearch.alerts_enabled.is_(True))):
        org = db.get(Organization, s.organization_id)
        if not billing.plan_of(org)["alerts"]:
            continue
        since = s.last_alert_check_at or s.created_at
        since = since if since.tzinfo else since.replace(tzinfo=UTC)
        kw = _query_kwargs(s.filters or {})
        kw["verified_from"] = max(kw.get("verified_from") or since, since)
        kw.pop("sort", None)
        rows, _ = stores.list_stores(db, **kw, sort="newest", page=1, page_size=limit_per_search)
        seen = set(db.scalars(select(Alert.store_id).where(Alert.saved_search_id == s.id)))
        for store, _domain, _n in rows:
            if store.id not in seen:
                db.add(Alert(organization_id=s.organization_id, saved_search_id=s.id, store_id=store.id, created_at=now))
                created += 1
        s.last_alert_check_at = now
        db.commit()
    return created


def list_alerts(db: Session, org: Organization, unread_only: bool, limit: int = 100):
    q = (
        select(Alert, SavedSearch.name, Domain.normalized_domain, Store.store_name)
        .join(SavedSearch, SavedSearch.id == Alert.saved_search_id)
        .join(Store, Store.id == Alert.store_id)
        .join(Domain, Domain.id == Store.domain_id)
        .where(Alert.organization_id == org.id)
    )
    if unread_only:
        q = q.where(Alert.read_at.is_(None))
    return db.execute(q.order_by(Alert.created_at.desc()).limit(limit)).all()


def mark_alerts_read(db: Session, org: Organization) -> int:
    r = db.execute(update(Alert).where(Alert.organization_id == org.id, Alert.read_at.is_(None)).values(read_at=datetime.now(UTC)))
    db.commit()
    return r.rowcount or 0


# ---------- exports ----------


ADMIN_EXPORT_MAX = 500_000  # ponytail: CSV is built in memory (~150 B/row full, ~25 B/row domains); stream if this grows
# Rows per downloadable file: one response must stay under Vercel's 4.5 MB body cap (full ≈ 155 B/row, domains ≈ 25 B/row).
PART_ROWS = {"domains": 100_000, "full": 20_000}


def _positive_int(v, name: str) -> int | None:
    v = str(v or "").strip()
    if not v:
        return None
    if not v.isdigit() or int(v) < 1:
        raise ValueError(f"{name} must be a positive whole number")
    return int(v)


def export_csv(db: Session, org: Organization, filters: dict, unlimited: bool = False) -> tuple[str, int, bool]:
    """→ (csv text, rows, truncated). Counts against the plan's monthly export quota unless `unlimited` (admins).
    Extra params: `limit` = max rows wanted; `columns=domains` = one `domain` column only;
    `part=k` = only the k-th file of PART_ROWS rows (for downloads too big for one file);
    `new_only=1` = skip stores this organization already downloaded. Every downloaded store is recorded."""
    filters = filters or {}
    kw = _query_kwargs(clean_filters(filters))
    new_only = str(filters.get("new_only") or "").lower() in ("1", "true", "on", "yes")
    if new_only:
        kw["not_exported_by"] = org.id
    _, total = stores.list_stores(db, **kw, page=1, page_size=1)
    allowed = min(total, ADMIN_EXPORT_MAX) if unlimited else billing.check_export(db, org, total)
    limit, part = _positive_int(filters.get("limit"), "limit"), _positive_int(filters.get("part"), "part")
    if limit:
        allowed = min(allowed, limit)
    domains_only = filters.get("columns") == "domains"
    size = PART_ROWS["domains" if domains_only else "full"]
    pos = (part - 1) * size if part else 0  # index of the first row of the current page
    end = min(allowed, pos + size) if part else allowed
    page = part or 1
    if part and new_only:  # earlier parts are already recorded as downloaded: part k is simply the next rows
        pos, page = 0, 1
        end = max(0, min(size, allowed, (limit - (part - 1) * size) if limit else size))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(("domain",) if domains_only else EXPORT_COLUMNS)
    ids: list[uuid.UUID] = []
    while pos < end:  # big pages: every page re-runs the grouped query, so fewer pages = much faster
        rows, _ = stores.list_stores(db, **kw, page=page, page_size=size)
        for st, dom, n in rows[: end - pos]:
            w.writerow([dom] if domains_only else [
                dom, _safe(st.store_name), st.primary_platform, st.platform_confidence, st.ecommerce_status,
                st.activity_status, st.business_country, st.first_verified_at and st.first_verified_at.isoformat(),
                st.last_verified_at and st.last_verified_at.isoformat(), n])  # fmt: skip
            ids.append(st.id)
        if len(rows) < size:
            break
        pos += size
        page += 1
    written = len(ids)
    for k in range(0, written, 5000):  # remember what this organization has downloaded
        db.execute(
            pg_insert(ExportedStore)
            .values([{"id": uuid.uuid4(), "organization_id": org.id, "store_id": i} for i in ids[k : k + 5000]])
            .on_conflict_do_nothing(constraint="uq_exported_stores_organization_id")
        )
    billing.add_usage(db, org, "exports")
    billing.add_usage(db, org, "export_rows", written)
    db.commit()
    return buf.getvalue(), written, total > written


def _safe(v: str | None) -> str:
    """CSV/formula injection guard: spreadsheet apps execute cells starting with = + - @."""
    v = v or ""
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


# ---------- admin ----------


def admin_overview(db: Session) -> dict:
    orgs = db.execute(
        select(Organization, func.count(Membership.id))
        .outerjoin(Membership, Membership.organization_id == Organization.id)
        .group_by(Organization.id)
        .order_by(Organization.created_at.desc())
    ).all()
    p = billing.period()
    usage = {(u.organization_id, u.metric): u.count for u in db.scalars(select(UsageCounter).where(UsageCounter.period == p))}
    users = db.scalars(select(User).order_by(User.created_at.desc()).limit(500)).all()
    return {
        "organizations": [
            {"id": o.id, "name": o.name, "plan": o.plan, "subscription_status": o.subscription_status, "members": n,
             "exports_this_month": usage.get((o.id, "exports"), 0), "created_at": o.created_at}
            for o, n in orgs
        ],
        "users": [
            {"id": u.id, "email": u.email, "name": u.name, "is_admin": u.is_admin, "is_active": u.is_active,
             "last_login_at": u.last_login_at, "created_at": u.created_at}
            for u in users
        ],
        "active_sessions": db.scalar(select(func.count()).select_from(AuthSession).where(AuthSession.expires_at > datetime.now(UTC))),
        "suppressed_domains": db.scalar(select(func.count()).select_from(Domain).where(Domain.suppressed_at.is_not(None))),
    }  # fmt: skip
