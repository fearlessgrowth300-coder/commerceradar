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

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session

from app.models import DiscoveredUrl, DiscoverySource, Store, StoreDomain


def _linked_domains():
    return select(StoreDomain.domain_id).where(
        StoreDomain.store_id == Store.id, StoreDomain.valid_to.is_(None), StoreDomain.role.in_(("primary", "alias"))
    )


def restricted_store_clause():
    """SQL boolean, correlated to `Store`: True when the store depends only on restricted sources."""
    linked = _linked_domains()
    in_linked = DiscoveredUrl.domain_id.in_(linked) | (DiscoveredUrl.domain_id == Store.domain_id)
    restricted_src = select(DiscoverySource.id).where(DiscoverySource.restricted.is_(True))
    open_src = select(DiscoverySource.id).where(DiscoverySource.restricted.is_(False))
    has_restricted = exists().where(in_linked, DiscoveredUrl.source_id.in_(restricted_src))
    has_open = exists().where(in_linked, DiscoveredUrl.source_id.in_(open_src))
    return and_(has_restricted, ~has_open)


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
