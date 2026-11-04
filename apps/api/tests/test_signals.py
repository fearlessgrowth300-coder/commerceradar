"""M7: evidence-based signals, store directory / details API."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import (CrawlJob, DiscoveredUrl, DiscoverySource, Domain, Product, ProductObservation, Store,
                        StoreObservation, StoreSignal)  # fmt: skip
from app.services import signals


def make_store(db, **kw) -> Store:
    d = Domain(normalized_domain=f"s{uuid.uuid4().hex[:10]}.example", verification_status="confirmed_ecommerce")
    db.add(d)
    db.flush()
    s = Store(domain_id=d.id, **{"ecommerce_status": "confirmed_ecommerce", "activity_status": "active",
                                 "consecutive_failures": 0, **kw})  # fmt: skip
    db.add(s)
    db.commit()
    return s


def add_products(db, store, n, seen, method="json-ld", status="active"):
    out = []
    for _ in range(n):
        p = Product(store_id=store.id, product_url=f"https://x.example/products/{uuid.uuid4().hex}", title="P",
                    current_price=Decimal("1.00"), first_seen_at=seen, last_seen_at=seen, status=status,
                    extraction_method=method)  # fmt: skip
        db.add(p)
        out.append(p)
    db.commit()
    return out


def verify_job(db, store, hits, url="https://s.example/", year=None):
    d = db.get(Domain, store.domain_id)
    db.add(CrawlJob(domain_id=d.id, target_domain=d.normalized_domain, job_type="verify", status="succeeded",
                    completed_at=datetime.now(UTC),
                    result={"status": "confirmed_ecommerce", "evidence": {
                        "score": {"signals_hit": hits}, "homepage": {"url": url, "signals": {"copyright_year": year}}}}))  # fmt: skip
    db.commit()


def types(db, store):
    return {s.type for s in signals.compute(db, store, datetime.now(UTC))}


@pytest.mark.parametrize("text,year", [("© 2019 Acme", 2019), ("Copyright 2015-2023 Acme", 2023), ("(c) 2021", 2021),
                                       ("call 2019 times", None), ("© 1850", None)])  # fmt: skip
def test_copyright_year(text, year):
    assert signals.copyright_year(text) == year


def test_established_healthy_store_has_no_signals(db):
    s = make_store(db, first_verified_at=datetime.now(UTC) - timedelta(days=200))
    add_products(db, s, 6, datetime.now(UTC) - timedelta(days=100))
    verify_job(db, s, ["product_structured_data", "add_to_cart"], year=datetime.now(UTC).year)
    assert types(db, s) == set()


def test_growth_signals(db):
    now = datetime.now(UTC)
    s = make_store(db, first_verified_at=now - timedelta(days=3))
    add_products(db, s, 20, now - timedelta(days=40))
    add_products(db, s, 12, now - timedelta(days=2))
    crt = DiscoverySource(name=f"crt-{uuid.uuid4().hex[:6]}", source_type="crtsh", configuration={}, state={})
    db.add(crt)
    db.flush()
    d = db.get(Domain, s.domain_id)
    db.add(DiscoveredUrl(normalized_url=f"https://{d.normalized_domain}/", hostname=d.normalized_domain, source_id=crt.id,
                         domain_id=d.id, first_discovered_at=now - timedelta(days=20), last_discovered_at=now))  # fmt: skip
    db.commit()
    got = {x.type: x for x in signals.compute(db, s, now)}
    assert {"newly_verified_store", "recent_first_certificate", "rapid_catalogue_expansion"} <= set(got)
    assert got["rapid_catalogue_expansion"].evidence["new_products"] == 12
    assert "not a store launch date" in got["recent_first_certificate"].summary
    assert got["recent_first_certificate"].evidence["first_certificate_observed_at"]
    assert signals.SIGNAL_TYPES["recent_first_certificate"] == ("change", "observed")


def test_first_extraction_is_not_expansion(db):
    now = datetime.now(UTC)
    s = make_store(db, first_verified_at=now - timedelta(days=60))
    add_products(db, s, 30, now - timedelta(hours=5))  # first ever extraction, all "new"
    assert "rapid_catalogue_expansion" not in types(db, s)


def test_change_health_and_opportunity_signals(db):
    now = datetime.now(UTC)
    s = make_store(db, first_verified_at=now - timedelta(days=200), consecutive_failures=2,
                   activity_evidence={"check": "temporarily_unreachable", "failure_kind": "transient"})  # fmt: skip
    ps = add_products(db, s, 4, now - timedelta(days=100), method="opengraph")
    ps += add_products(db, s, 6, now - timedelta(days=100), method="opengraph", status="removed")
    for p in ps[:3]:
        db.add(ProductObservation(product_id=p.id, observed_at=now - timedelta(days=1), change_type="changed"))
    db.add(StoreObservation(store_id=s.id, observation_type="platform_change", observed_at=now - timedelta(days=5),
                            observation_data={"from": "woocommerce", "to": "shopify"}))  # fmt: skip
    db.commit()
    verify_job(db, s, ["prices", "product_links"], url="http://legacy.example/", year=now.year - 5)
    got = types(db, s)
    assert got >= {"catalogue_changes", "technology_change", "site_check_failures", "many_products_removed",
                   "missing_product_structured_data", "missing_cart_functionality", "no_https", "outdated_copyright_notice"}  # fmt: skip


def test_refresh_detects_resolves_and_keeps_history(db):
    now = datetime.now(UTC)
    s = make_store(db, first_verified_at=now - timedelta(days=3))
    r1 = signals.refresh_store(db, s, now)
    db.commit()
    assert r1 == {"detected": 1, "resolved": 0, "active": 1}
    s.first_verified_at = now - timedelta(days=90)  # window passes
    r2 = signals.refresh_store(db, s, now + timedelta(days=1))
    db.commit()
    assert r2["resolved"] == 1
    row = db.scalar(select(StoreSignal).where(StoreSignal.store_id == s.id))
    assert row.active is False and row.resolved_at is not None  # kept, not deleted
    kinds = [o.observation_type for o in db.scalars(select(StoreObservation).where(StoreObservation.store_id == s.id)
                                                   .order_by(StoreObservation.observed_at))]  # fmt: skip
    assert kinds == ["signal_detected", "signal_resolved"]
    assert signals.refresh_store(db, s, now + timedelta(days=2)) == {"detected": 0, "resolved": 0, "active": 0}


# ---------- API ----------


def test_store_directory_filters_and_detail(db, client):
    now = datetime.now(UTC)
    a = make_store(db, first_verified_at=now - timedelta(days=2), primary_platform="shopify", store_name="Alpha Gear")
    b = make_store(db, first_verified_at=now - timedelta(days=50), primary_platform="woocommerce", activity_status="inactive")
    add_products(db, a, 3, now)
    signals.refresh_store(db, a, now)
    db.commit()

    def ids(**params):
        return [r["id"] for r in client.get("/api/v1/stores", params={"page_size": 200, **params}).json()["items"]]

    assert str(a.id) in ids(platform="shopify") and str(b.id) not in ids(platform="shopify")
    assert str(b.id) in ids(activity="inactive") and str(a.id) not in ids(activity="inactive")
    assert ids(q="alpha gear") == [str(a.id)]
    assert str(a.id) in ids(signal="newly_verified_store") and str(b.id) not in ids(signal="newly_verified_store")
    recent = ids(verified_from=(now - timedelta(days=10)).isoformat())
    assert str(a.id) in recent and str(b.id) not in recent
    row = next(r for r in client.get("/api/v1/stores", params={"q": "alpha gear"}).json()["items"])
    assert row["product_count"] == 3
    for bad in ({"platform": "nope"}, {"activity": "dead"}, {"signal": "rich"}, {"sort": "revenue"}):
        assert client.get("/api/v1/stores", params=bad).status_code == 422

    detail = client.get(f"/api/v1/stores/{a.id}").json()
    assert detail["store"]["store_name"] == "Alpha Gear" and detail["product_count"] == 3
    assert detail["signals"][0]["signal_type"] == "newly_verified_store"
    obs = client.get(f"/api/v1/stores/{a.id}/observations").json()
    assert obs["items"][0]["observation_type"] == "signal_detected"
    assert client.get(f"/api/v1/stores/{uuid.uuid4()}").status_code == 404

    plats = {p["platform"]: p["stores"] for p in client.get("/api/v1/platforms").json()}
    assert plats["shopify"] >= 1 and plats["woocommerce"] >= 1
    sig = client.get("/api/v1/signals", params={"type": "newly_verified_store"}).json()
    assert any(r["store_id"] == str(a.id) and r["basis"] == "observed" for r in sig["items"])
    assert any(t["type"] == "recent_first_certificate" and t["basis"] == "observed" for t in client.get("/api/v1/signals/types").json())
    act = client.get("/api/v1/dashboard/activity").json()
    assert {"platforms", "recent_runs", "recent_changes", "signals"} <= set(act)
    assert "new_stores_7d" in client.get("/api/v1/dashboard/overview").json()


def test_discovery_run_routes(client, monkeypatch):
    from app.api import v1

    monkeypatch.setattr(v1, "_enqueue_run", lambda rid, seq: None)
    src = next(s for s in client.get("/api/v1/discovery/sources").json() if s["name"] == "commerceradar_crawler")
    r = client.post("/api/v1/discovery/runs", json={"source_id": src["id"]})
    if r.status_code == 409:  # a scheduled run may already be active in a shared test DB
        return
    assert r.status_code == 202
    got = client.get(f"/api/v1/discovery/runs/{r.json()['id']}").json()
    assert got["id"] == r.json()["id"]
    assert client.get(f"/api/v1/discovery/runs/{uuid.uuid4()}").status_code == 404
    from app.db import SessionLocal
    from app.models import DiscoveryRun

    with SessionLocal() as s:
        s.get(DiscoveryRun, uuid.UUID(got["id"])).status = "cancelled"
        s.commit()
