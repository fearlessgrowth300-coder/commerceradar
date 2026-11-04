"""M11 priority 3: urlscan quarantine — kept for provenance, hidden from customers and exports."""

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import DiscoveredUrl, DiscoverySource, Domain, Store, StoreDomain
from app.services import discovery, provenance


def tag():
    return uuid.uuid4().hex[:8]


def customer():
    e = f"c{tag()}@tests.example"
    tok = TestClient(app).post("/api/v1/auth/register", json={"email": e, "password": "a long password"}).json()["token"]
    return TestClient(app, headers={"Authorization": f"Bearer {tok}"})


def store_found_by(db, sources: list[DiscoverySource], host=None) -> Store:
    d = Domain(normalized_domain=host or f"p{tag()}.example", verification_status="confirmed_ecommerce")
    db.add(d)
    db.flush()
    for s in sources:
        db.add(DiscoveredUrl(normalized_url=f"https://{d.normalized_domain}/", hostname=d.normalized_domain, source_id=s.id, domain_id=d.id))
    st = Store(domain_id=d.id, ecommerce_status="confirmed_ecommerce", activity_status="active", consecutive_failures=0,
               first_verified_at=datetime.now(UTC), primary_platform="shopify", store_name=f"Shop {tag()}")  # fmt: skip
    db.add(st)
    db.flush()
    db.add(StoreDomain(store_id=st.id, domain_id=d.id, role="primary", alias_type="self", confidence=1.0))
    db.commit()
    return st


def test_urlscan_is_restricted_by_migration(db):
    us = db.scalar(select(DiscoverySource).where(DiscoverySource.source_type == "urlscan"))
    assert us.restricted and not us.enabled and "written permission" in us.restriction_reason


def test_restricted_only_stores_hidden_from_customers_and_exports_but_kept(db, client):
    restricted = DiscoverySource(name=f"r-{tag()}", source_type="urlscan", configuration={}, state={}, restricted=True, restriction_reason="x")
    open_src = DiscoverySource(name=f"o-{tag()}", source_type="commoncrawl", configuration={}, state={})
    db.add_all([restricted, open_src])
    db.commit()
    only_r = store_found_by(db, [restricted])
    both = store_found_by(db, [restricted, open_src])  # also found by a permitted source
    manual = store_found_by(db, [])  # manual submission
    assert provenance.restricted_ids(db, [only_r.id, both.id, manual.id]) == {only_r.id}

    cust = customer()
    ids = {r["id"] for r in cust.get("/api/v1/stores", params={"page_size": 200, "q": "shop "}).json()["items"]}
    assert str(both.id) in ids and str(manual.id) in ids and str(only_r.id) not in ids
    assert cust.get(f"/api/v1/stores/{only_r.id}").status_code == 404
    assert cust.get(f"/api/v1/stores/{only_r.id}/products").status_code == 404
    csv_text = cust.get("/api/v1/exports/stores.csv").text
    assert db.get(Domain, only_r.domain_id).normalized_domain not in csv_text

    # admin still sees it, flagged; nothing deleted, provenance unchanged
    row = next(r for r in client.get("/api/v1/stores", params={"page_size": 200, "q": only_r.store_name}).json()["items"])
    assert row["restricted"] is True
    assert client.get(f"/api/v1/stores/{only_r.id}").json()["restricted"] is True
    assert db.scalar(select(DiscoveredUrl.source_id).where(DiscoveredUrl.domain_id == only_r.domain_id)) == restricted.id

    # restricted sources cannot be run
    import pytest

    with pytest.raises(discovery.RunConflict, match="restricted"):
        discovery.launch_run(db, restricted, lambda r, s: None)
    assert client.get("/api/v1/admin/source-rights").json()["restricted_live_stores"] >= 1
