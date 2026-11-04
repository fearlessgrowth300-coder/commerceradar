"""Evidence-based intelligence signals (spec milestone 7).

Every signal states what was *measured* and carries the raw evidence. `basis` is
"observed" (a fact we recorded) or "inferred" (an interpretation, said so in the summary).
We never claim revenue, sales, traffic or that a business is struggling/profitable.
Signals are kept as rows per (store, type): when one stops applying it is resolved, not
deleted, and both transitions are written to store_observations.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CrawlJob, DiscoveredUrl, DiscoverySource, Product, ProductObservation, Store, StoreObservation, StoreSignal

COPYRIGHT = re.compile(r"(?:©|\(c\)|copyright)\s*(?:\d{4}\s*[-–]\s*)?((?:19|20)\d{2})\b", re.I)

# type → (category, basis)
SIGNAL_TYPES: dict[str, tuple[str, str]] = {
    "newly_verified_store": ("growth", "observed"),
    "possibly_recently_launched": ("growth", "inferred"),
    "rapid_catalogue_expansion": ("growth", "observed"),
    "catalogue_changes": ("change", "observed"),
    "technology_change": ("change", "observed"),
    "missing_cart_functionality": ("opportunity", "observed"),
    "missing_product_structured_data": ("opportunity", "observed"),
    "no_https": ("opportunity", "observed"),
    "outdated_copyright_notice": ("opportunity", "observed"),
    "site_check_failures": ("health", "observed"),
    "many_products_removed": ("health", "observed"),
}

NEW_STORE_DAYS = 30
LAUNCH_CERT_DAYS = 90
WINDOW_DAYS = 14


@dataclass
class Signal:
    type: str
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)


def copyright_year(text: str) -> int | None:
    years = [int(y) for y in COPYRIGHT.findall(text)]
    return max(years) if years else None


def _aware(dt: datetime | None) -> datetime | None:
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=UTC)


def _d(dt: datetime) -> str:
    return dt.date().isoformat()


def compute(db: Session, store: Store, now: datetime) -> list[Signal]:
    out: list[Signal] = []
    first_verified = _aware(store.first_verified_at)

    if first_verified and now - first_verified <= timedelta(days=NEW_STORE_DAYS):
        out.append(Signal("newly_verified_store", f"First verified as a store by CommerceRadar on {_d(first_verified)}.",
                          {"first_verified_at": first_verified.isoformat()}))  # fmt: skip

    # Earliest certificate CT logs showed us for this domain (crt.sh source records not_before).
    cert = db.scalar(
        select(func.min(DiscoveredUrl.first_discovered_at))
        .join(DiscoverySource, DiscoverySource.id == DiscoveredUrl.source_id)
        .where(DiscoveredUrl.domain_id == store.domain_id, DiscoverySource.source_type == "crtsh")
    )
    cert = _aware(cert)
    if cert and now - cert <= timedelta(days=LAUNCH_CERT_DAYS):
        out.append(Signal("possibly_recently_launched",
                          f"Earliest TLS certificate we have seen dates from {_d(cert)}. The store may be new "
                          "(inferred: older certificates may exist that we have not seen).",
                          {"earliest_certificate_seen": cert.isoformat()}))  # fmt: skip

    # Catalogue: only what our sampled extractions observed — never a claim about the full catalogue.
    since = now - timedelta(days=WINDOW_DAYS)
    total = db.scalar(select(func.count()).select_from(Product).where(Product.store_id == store.id)) or 0
    baseline = db.scalar(select(func.min(Product.first_seen_at)).where(Product.store_id == store.id))
    if total and baseline:
        baseline_end = max(_aware(baseline) + timedelta(days=1), since)
        added = db.scalar(select(func.count()).select_from(Product)
                          .where(Product.store_id == store.id, Product.first_seen_at >= baseline_end)) or 0  # fmt: skip
        before = total - added
        if added >= 10 and before and added >= 0.2 * before:
            out.append(Signal("rapid_catalogue_expansion",
                              f"{added} products first observed in the last {WINDOW_DAYS} days, versus {before} observed before.",
                              {"new_products": added, "previously_observed": before, "window_days": WINDOW_DAYS,
                               "note": "counts cover the product pages we sampled, not necessarily the full catalogue"}))  # fmt: skip

    changes = dict(
        db.execute(
            select(ProductObservation.change_type, func.count())
            .join(Product, Product.id == ProductObservation.product_id)
            .where(Product.store_id == store.id, ProductObservation.observed_at >= since,
                   ProductObservation.change_type.in_(("changed", "removed", "reappeared")))
            .group_by(ProductObservation.change_type)
        ).tuples().all()
    )  # fmt: skip
    if sum(changes.values()) >= 3:
        out.append(Signal("catalogue_changes",
                          f"{sum(changes.values())} product changes observed in the last {WINDOW_DAYS} days "
                          f"({', '.join(f'{v} {k}' for k, v in sorted(changes.items()))}).",
                          {"window_days": WINDOW_DAYS, **changes}))  # fmt: skip

    removed = db.scalar(select(func.count()).select_from(Product).where(Product.store_id == store.id, Product.status == "removed")) or 0
    if removed >= 5 and total and removed >= 0.3 * total:
        out.append(Signal("many_products_removed", f"{removed} of {total} observed product pages now return 404/410.",
                          {"removed": removed, "observed_products": total}))  # fmt: skip

    if total >= 5:
        methods = dict(db.execute(select(Product.extraction_method, func.count()).where(Product.store_id == store.id)
                                  .group_by(Product.extraction_method)).tuples().all())  # fmt: skip
        if not methods.get("json-ld"):
            out.append(Signal("missing_product_structured_data",
                              f"None of {total} observed product pages expose schema.org Product data.",
                              {"extraction_methods": methods}))  # fmt: skip

    platform_change = db.scalar(
        select(StoreObservation).where(StoreObservation.store_id == store.id, StoreObservation.observation_type == "platform_change",
                                       StoreObservation.observed_at >= now - timedelta(days=90))
        .order_by(StoreObservation.observed_at.desc()).limit(1)
    )  # fmt: skip
    if platform_change:
        d = platform_change.observation_data
        out.append(Signal("technology_change", f"Platform changed from {d.get('from')} to {d.get('to')} on {_d(_aware(platform_change.observed_at))}.",
                          {**d, "observed_at": _aware(platform_change.observed_at).isoformat()}))  # fmt: skip

    if (store.consecutive_failures or 0) >= 2:
        ev = store.activity_evidence or {}
        out.append(Signal("site_check_failures", f"The last {store.consecutive_failures} checks did not confirm the store ({ev.get('check', 'unknown')}).",
                          {"consecutive_failures": store.consecutive_failures, "last_check": ev.get("check"),
                           "failure_kind": ev.get("failure_kind")}))  # fmt: skip

    # Latest successful verification evidence (what the homepage showed).
    job = db.scalar(
        select(CrawlJob).where(CrawlJob.domain_id == store.domain_id, CrawlJob.job_type == "verify", CrawlJob.status == "succeeded",
                               CrawlJob.result["status"].as_string().in_(("confirmed_ecommerce", "probable_ecommerce")))
        .order_by(CrawlJob.completed_at.desc()).limit(1)
    )  # fmt: skip
    ev = (job.result or {}).get("evidence", {}) if job else {}
    hits = set((ev.get("score") or {}).get("signals_hit") or [])
    home = ev.get("homepage") or {}
    if job and not hits & {"add_to_cart", "cart_link"}:
        out.append(Signal("missing_cart_functionality", "No add-to-cart control or cart link was found on the pages checked.",
                          {"signals_hit": sorted(hits), "checked": [home.get("url"), (ev.get("product_page") or {}).get("url")]}))  # fmt: skip
    if str(home.get("url", "")).startswith("http://"):
        out.append(Signal("no_https", "The homepage was served over plain HTTP (no HTTPS).", {"homepage": home.get("url")}))
    year = (home.get("signals") or {}).get("copyright_year")
    if year and year <= now.year - 3:
        out.append(Signal("outdated_copyright_notice", f"The homepage copyright notice says {year}.",
                          {"copyright_year": year, "checked_year": now.year}))  # fmt: skip
    return out


def refresh_store(db: Session, store: Store, now: datetime) -> dict[str, int]:
    """Upsert current signals; resolve ones that no longer apply. Caller commits."""
    current = {s.type: s for s in compute(db, store, now)}
    rows = {r.signal_type: r for r in db.scalars(select(StoreSignal).where(StoreSignal.store_id == store.id))}
    detected = resolved = 0
    for t, sig in current.items():
        category, basis = SIGNAL_TYPES[t]
        row = rows.get(t)
        if row is None:
            row = StoreSignal(store_id=store.id, signal_type=t, category=category, basis=basis, first_detected_at=now)
            db.add(row)
        if row.active is not True:
            detected += 1
            _log(db, store, "signal_detected", {"type": t, "summary": sig.summary}, now)
        row.active, row.resolved_at, row.last_detected_at = True, None, now
        row.summary, row.evidence = sig.summary, sig.evidence
    for t, row in rows.items():
        if row.active and t not in current:
            row.active, row.resolved_at = False, now
            resolved += 1
            _log(db, store, "signal_resolved", {"type": t}, now)
    store.signals_refreshed_at = now
    return {"detected": detected, "resolved": resolved, "active": len(current)}


def _log(db: Session, store: Store, kind: str, data: dict, now: datetime) -> None:
    db.add(StoreObservation(store_id=store.id, observation_type=kind, observation_data=data, observed_at=now))


def list_signals(db: Session, signal_type: str | None, category: str | None, active: bool, page: int, page_size: int):
    from app.models import Domain

    q = (
        select(StoreSignal, Domain.normalized_domain, Store.store_name)
        .join(Store, Store.id == StoreSignal.store_id)
        .join(Domain, Domain.id == Store.domain_id)
        .where(StoreSignal.active.is_(active), Domain.suppressed_at.is_(None))
    )
    if signal_type:
        q = q.where(StoreSignal.signal_type == signal_type)
    if category:
        q = q.where(StoreSignal.category == category)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.execute(q.order_by(StoreSignal.last_detected_at.desc(), StoreSignal.id).offset((page - 1) * page_size).limit(page_size)).all()
    return rows, total


def counts(db: Session) -> dict[str, int]:
    q = select(StoreSignal.signal_type, func.count()).where(StoreSignal.active.is_(True)).group_by(StoreSignal.signal_type)
    return dict(db.execute(q).tuples().all())
