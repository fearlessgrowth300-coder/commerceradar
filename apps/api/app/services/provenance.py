"""Source-rights quarantine (M11 priority 3).

A discovery source can be marked `restricted` (e.g. urlscan: its terms require written permission
for commercial use and database compilation). Nothing is deleted or re-attributed. A store is
*restricted* when every domain currently linked to it (primary + aliases) was found ONLY by
restricted sources. If any unrestricted source (Common Crawl, crawler, ...) also found one of its
domains, it is not restricted. Manually submitted domains (no source record) are not restricted.

Restricted stores are excluded from customer-facing views, exports, alerts and aggregate
commercial intelligence; platform admins can still see them (flagged) for review.
"""

import uuid

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models import DiscoveredUrl, DiscoverySource, Store, StoreDomain


def _found_by(restricted: bool):
    """EXISTS: some domain of this store (current primary/alias link, or stores.domain_id) was found
    by a source with the given restricted flag. Single-level subqueries, explicitly correlated."""
    src = select(DiscoverySource.id).where(DiscoverySource.restricted.is_(restricted))
    via_links = (
        select(1)
        .select_from(DiscoveredUrl)
        .join(StoreDomain, StoreDomain.domain_id == DiscoveredUrl.domain_id)
        .where(StoreDomain.store_id == Store.id, StoreDomain.valid_to.is_(None),
               StoreDomain.role.in_(("primary", "alias")), DiscoveredUrl.source_id.in_(src))
        .correlate(Store)
    )  # fmt: skip
    direct = select(1).select_from(DiscoveredUrl).where(DiscoveredUrl.domain_id == Store.domain_id, DiscoveredUrl.source_id.in_(src)).correlate(Store)
    return via_links.exists() | direct.exists()


def restricted_store_clause():
    """SQL boolean, correlated to `Store`: True when the store depends only on restricted sources."""
    return and_(_found_by(True), ~_found_by(False))


def restricted_ids(db: Session, store_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not store_ids:
        return set()
    return set(db.scalars(select(Store.id).where(Store.id.in_(store_ids), restricted_store_clause())))


def is_restricted(db: Session, store_id: uuid.UUID) -> bool:
    return bool(restricted_ids(db, [store_id]))


def summary(db: Session) -> dict:
    from sqlalchemy import func

    sources = db.execute(select(DiscoverySource.name, DiscoverySource.restriction_reason).where(DiscoverySource.restricted.is_(True))).all()
    n = db.scalar(select(func.count()).select_from(Store).where(Store.merged_into_store_id.is_(None), restricted_store_clause())) or 0
    return {"restricted_sources": [{"source": s, "reason": r} for s, r in sources], "restricted_live_stores": n}
