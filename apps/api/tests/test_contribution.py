"""M12-A: unique-store contribution is alias-aware and counts live stores, not domains."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.models import DiscoveredUrl, DiscoverySource, Domain, MetricSnapshot, Store, StoreDomain
from app.services import metrics


def t():
    return uuid.uuid4().hex[:8]


def src(db, name):
    s = DiscoverySource(name=name, source_type="commoncrawl", configuration={}, state={})
    db.add(s)
    db.flush()
    return s


def domain(db, sources, status="confirmed_ecommerce"):
    d = Domain(normalized_domain=f"c{t()}.example", verification_status=status)
    db.add(d)
    db.flush()
    for s in sources:
        db.add(DiscoveredUrl(normalized_url=f"https://{d.normalized_domain}/", hostname=d.normalized_domain, source_id=s.id, domain_id=d.id))
    return d


def store(db, primary, aliases=(), merged_into=None):
    st = Store(domain_id=primary.id, ecommerce_status="confirmed_ecommerce", activity_status="active", consecutive_failures=0,
               first_verified_at=datetime.now(UTC), merged_into_store_id=merged_into)  # fmt: skip
    db.add(st)
    db.flush()
    db.add(StoreDomain(store_id=st.id, domain_id=primary.id, role="primary", alias_type="self", confidence=1.0))
    for a in aliases:
        db.add(StoreDomain(store_id=st.id, domain_id=a.id, role="alias", alias_type="platform_hosted", confidence=0.95))
    return st


def row(rows, name):
    return next(r for r in rows if r["source"] == name)


def test_unique_vs_shared_is_alias_aware(db):
    a, b = src(db, f"A-{t()}"), src(db, f"B-{t()}")
    only_a = store(db, domain(db, [a]))
    # B found the custom domain, A found the platform-hosted alias: one store, shared by A and B
    shared = store(db, domain(db, [b]), aliases=[domain(db, [a])])
    pending = domain(db, [a], status="pending")
    merged = store(db, domain(db, [b]), merged_into=only_a.id)  # folded duplicate: not counted
    db.commit()
    rows = metrics.unique_contribution(db)
    ra, rb = row(rows, a.name), row(rows, b.name)
    assert (ra["stores"], ra["unique_stores"], ra["shared_stores"]) == (2, 1, 1)
    assert (rb["stores"], rb["unique_stores"], rb["shared_stores"]) == (1, 0, 1)
    assert ra["candidates"] == 3 and ra["processed"] == 2  # pending candidate not processed
    assert ra["store_rate"] == 1.0 and merged.merged_into_store_id == only_a.id and pending.id


def test_daily_snapshot_is_idempotent(db):
    now = datetime(2031, 1, 2, 3, tzinfo=UTC)
    n = metrics.take_daily_snapshot(db, now)
    assert n > 0 and metrics.take_daily_snapshot(db, now) == 0
    assert db.scalar(select(func.count()).select_from(MetricSnapshot).where(MetricSnapshot.taken_on == "2031-01-02")) == n
    assert any(h["day"] == "2031-01-02" for h in metrics.history(db, "live_stores"))


def test_metrics_endpoint_includes_contribution(client):
    snap = client.get("/api/v1/admin/metrics").json()
    assert isinstance(snap["unique_contribution"], list)
    assert client.get("/api/v1/admin/metrics/history", params={"metric": "live_stores"}).status_code == 200
