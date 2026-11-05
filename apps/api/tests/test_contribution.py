"""M12-A: unique-store contribution is alias-aware and counts live stores, not domains."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select

from datetime import timedelta

from app.models import CrawlJob, DiscoveredUrl, DiscoverySource, Domain, MetricSnapshot, Store, StoreDomain
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



def test_processing_cost_is_split_not_double_counted(db):
    a, b = src(db, f"CA-{t()}"), src(db, f"CB-{t()}")
    shared_dom = domain(db, [a, b])
    only_a_dom = domain(db, [a])
    manual_dom = domain(db, [])
    future = datetime(2032, 6, 1, tzinfo=UTC)  # isolated window
    for d, kind, secs in ((shared_dom, "verify", 10), (shared_dom, "products", 30), (only_a_dom, "verify", 4), (manual_dom, "verify", 6)):
        db.add(CrawlJob(domain_id=d.id, target_domain=d.normalized_domain, job_type=kind, status="succeeded",
                        started_at=future - timedelta(seconds=secs), completed_at=future))  # fmt: skip
    db.commit()
    cost = metrics.processing_cost(db, future + timedelta(seconds=1), days=1)
    assert cost["actual_seconds"] == {"verify": 20, "products": 30}
    assert cost["unattributed_seconds"] == {"verify": 6, "products": 0}
    pa, pb = cost["per_source"][a.name], cost["per_source"][b.name]
    assert (pa["verify"], pa["products"]) == (9.0, 15.0)  # 10/2 + 4, 30/2
    assert (pb["verify"], pb["products"]) == (5.0, 15.0)
    shares = sum(v["verify"] + v["products"] for v in cost["per_source"].values()) + 6
    assert shares == 50  # shares + unattributed == actual total: nothing counted twice
