"""M10 Task 7: an opt-out holds across sources, subdomains, redirects, queues and views."""

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from app import tasks
from app.models import CrawlJob, DiscoveredUrl, DiscoverySource, Domain, Product, Store, StoreSignal, SuppressionEvent
from app.security.ssrf import BlockedDestination
from app.services import discovery, suppression, verification
from app.services.fetcher import SafeFetcher
from app.sources import Found

IP = "93.184.216.34"


@pytest.fixture
def optout(db, client):
    """A suppressed root; always un-suppressed afterwards so other tests see an empty list."""
    root = f"optout{uuid.uuid4().hex[:8]}.example"
    assert client.post("/api/v1/admin/suppressions", json={"domain": root, "reason": "owner request"}).status_code == 201
    yield root
    client.delete(f"/api/v1/admin/suppressions/{root}")


def test_parents_and_matching():
    assert suppression.parents("a.b.example.com") == ["a.b.example.com", "b.example.com", "example.com"]
    roots = {"example.com"}
    assert suppression.matches("shop.example.com", roots) and suppression.matches("www.example.com", roots)
    assert not suppression.matches("notexample.com", roots) and not suppression.matches("example.co", roots)


def test_ingest_drops_suppressed_root_and_subdomains_from_any_source(db, optout):
    src = DiscoverySource(name=f"s-{uuid.uuid4().hex[:6]}", source_type="crtsh", configuration={}, state={})
    db.add(src)
    db.commit()
    run = discovery.start_run(db, src)
    ok = f"fine{uuid.uuid4().hex[:6]}.example"
    found = [Found(f"https://{optout}/"), Found(f"https://shop.{optout}/x"), Found(f"https://{ok}/")]
    stats = discovery.ingest(db, src.id, run, found, verify_budget=10, enqueue=lambda j: None)
    assert stats["suppressed"] == 2 and stats["urls"] == 1
    assert not db.scalars(select(DiscoveredUrl).where(DiscoveredUrl.hostname.like(f"%{optout}"))).all()
    assert db.scalar(select(Domain).where(Domain.normalized_domain == f"shop.{optout}")) is None


def test_suppressing_parent_covers_existing_subdomains_and_unsuppress_reverses_only_that(db, client):
    root = f"par{uuid.uuid4().hex[:8]}.example"
    sub = Domain(normalized_domain=f"store.{root}")
    own = Domain(normalized_domain=f"own.{root}", suppressed_at=datetime.now(UTC), suppression_reason="separate request")
    db.add_all([sub, own])
    db.flush()
    job = CrawlJob(domain_id=sub.id, target_domain=sub.normalized_domain, job_type="verify", status="queued")
    db.add(job)
    db.commit()

    assert client.post("/api/v1/admin/suppressions", json={"domain": root}).status_code == 201
    db.refresh(sub), db.refresh(job)
    assert sub.suppressed_at is not None and sub.suppression_reason == suppression.COVERED + root
    assert job.status == "failed"
    listed = [x["domain"] for x in client.get("/api/v1/admin/suppressions").json()]
    assert root in listed and sub.normalized_domain not in listed  # covered rows aren't separate opt-outs

    assert client.delete(f"/api/v1/admin/suppressions/{root}").status_code == 204
    db.refresh(sub), db.refresh(own)
    assert sub.suppressed_at is None
    assert own.suppressed_at is not None  # its own opt-out is untouched
    ev = [(e["action"], e["subdomains"]) for e in client.get("/api/v1/admin/suppressions/events").json() if e["domain"] == root]
    assert ev == [("unsuppress", 1), ("suppress", 1)]
    assert all(e["actor_user_id"] for e in client.get("/api/v1/admin/suppressions/events").json() if e["domain"] == root)


def test_fetcher_refuses_suppressed_host_and_redirects_into_it():
    seen = []

    def handler(request):
        seen.append(request.headers["host"])
        if request.headers["host"] == "front.example":
            return httpx.Response(301, headers={"location": "https://shop.banned.example/"})
        return httpx.Response(200, text="hi")

    f = SafeFetcher(lambda h: [IP], httpx.MockTransport(handler))
    f.blocked = lambda h: suppression.matches(h, {"banned.example"})
    with pytest.raises(BlockedDestination, match="suppressed"):
        f.fetch("https://banned.example/")
    with pytest.raises(BlockedDestination, match="suppressed"):
        f.fetch("https://front.example/")
    assert seen == ["front.example"]  # the suppressed host was never contacted


def test_verification_task_never_fetches_suppressed_redirect_target(db, client, monkeypatch, optout):
    from app.api import v1

    monkeypatch.setattr(v1, "_enqueue", lambda j: None)
    front = f"front{uuid.uuid4().hex[:6]}.example"
    contacted = []

    def handler(request):
        contacted.append(request.headers["host"])
        return httpx.Response(301, headers={"location": f"https://www.{optout}/"})

    monkeypatch.setattr(tasks, "SafeFetcher", lambda: SafeFetcher(lambda h: [IP], httpx.MockTransport(handler)))
    job_id = client.post("/api/v1/discovery/domains", json={"domain": front}).json()["job"]["id"]
    assert tasks.verify_domain.apply(args=[job_id]).result == "blocked"
    assert all(optout not in h for h in contacted)
    assert client.post("/api/v1/discovery/domains", json={"domain": f"deep.shop.{optout}"}).status_code == 422


def test_suppressed_store_hidden_from_every_ordinary_view(db, client):
    root = f"hide{uuid.uuid4().hex[:8]}.example"
    d = Domain(normalized_domain=root, verification_status="confirmed_ecommerce")
    db.add(d)
    db.flush()
    s = Store(domain_id=d.id, ecommerce_status="confirmed_ecommerce", activity_status="active", consecutive_failures=0,
              primary_platform="opencart", first_verified_at=datetime.now(UTC))  # fmt: skip
    db.add(s)
    db.flush()
    p = Product(store_id=s.id, product_url=f"https://{root}/products/a", title="A")
    db.add(p)
    db.add(StoreSignal(store_id=s.id, signal_type="newly_verified_store", category="growth", basis="observed",
                       summary="x", evidence={}, active=True))  # fmt: skip
    db.commit()
    assert client.get(f"/api/v1/domains/{d.id}").status_code == 200

    client.post("/api/v1/admin/suppressions", json={"domain": root})
    try:
        assert client.get(f"/api/v1/domains/{d.id}").status_code == 404
        assert all(x["id"] != str(d.id) for x in client.get("/api/v1/domains", params={"q": root}).json()["items"])
        assert client.get(f"/api/v1/stores/{s.id}").status_code == 404
        assert client.get(f"/api/v1/stores/{s.id}/products").status_code == 404
        assert client.get(f"/api/v1/stores/{s.id}/observations").status_code == 404
        assert client.get(f"/api/v1/products/{p.id}").status_code == 404
        assert all(x["store_id"] != str(s.id) for x in client.get("/api/v1/signals", params={"page_size": 200}).json()["items"])
        assert all(c["store_id"] != str(s.id) for c in client.get("/api/v1/dashboard/activity").json()["recent_changes"])
        assert root in [x["domain"] for x in client.get("/api/v1/admin/suppressions").json()]  # admin still sees it
        assert db.scalars(select(SuppressionEvent).where(SuppressionEvent.domain == root)).all()
    finally:
        client.delete(f"/api/v1/admin/suppressions/{root}")
    assert client.get(f"/api/v1/stores/{s.id}").status_code == 200


def test_verify_reports_blocked_reason_for_suppressed_redirect():
    def handler(request):
        return httpx.Response(301, headers={"location": "https://x.gone.example/"})

    f = SafeFetcher(lambda h: [IP], httpx.MockTransport(handler))
    f.blocked = lambda h: suppression.matches(h, {"gone.example"})
    r = verification.verify("front.example", f)
    assert r.status == "blocked" and "suppressed" in r.evidence["reason"]
