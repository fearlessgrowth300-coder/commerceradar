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

from sqlalchemy import select, union
from sqlalchemy.orm import Session, aliased

from app.models import DiscoveredUrl, DiscoverySource, Store, StoreDomain


def _sources(restricted: bool):
    return select(DiscoverySource.id).where(DiscoverySource.restricted.is_(restricted))


def _found_by(restricted: bool, st=Store):
    """EXISTS: some domain of store `st` (current primary/alias link, or stores.domain_id) was found
    by a source with the given restricted flag. Single-level subqueries, explicitly correlated."""
    via_links = (
        select(1)
        .select_from(DiscoveredUrl)
        .join(StoreDomain, StoreDomain.domain_id == DiscoveredUrl.domain_id)
        .where(StoreDomain.store_id == st.id, StoreDomain.valid_to.is_(None),
               StoreDomain.role.in_(("primary", "alias")), DiscoveredUrl.source_id.in_(_sources(restricted)))
        .correlate(st)
    )  # fmt: skip
    direct = (select(1).select_from(DiscoveredUrl)
              .where(DiscoveredUrl.domain_id == st.domain_id, DiscoveredUrl.source_id.in_(_sources(restricted))).correlate(st))  # fmt: skip
    return via_links.exists() | direct.exists()


def restricted_store_clause():
    """SQL boolean on `Store`: True when the store depends only on restricted sources.
    Built from the (few) domains restricted sources found, as one uncorrelated id set: a correlated
    EXISTS per store cost ~4 s per query at 194k stores."""
    st = aliased(Store)
    found = union(
        select(st.id).join(DiscoveredUrl, DiscoveredUrl.domain_id == st.domain_id).where(DiscoveredUrl.source_id.in_(_sources(True))),
        select(StoreDomain.store_id).join(DiscoveredUrl, DiscoveredUrl.domain_id == StoreDomain.domain_id)
        .where(StoreDomain.valid_to.is_(None), StoreDomain.role.in_(("primary", "alias")), DiscoveredUrl.source_id.in_(_sources(True))),
    ).subquery()  # fmt: skip
    only_restricted = select(st.id).where(st.id.in_(select(found.c[0])), ~_found_by(False, st))
    return Store.id.in_(only_restricted)


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
