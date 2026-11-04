"""M10 phases 3-4: Common Crawl myshopify source, provider refs, inactive storefronts,
crt.sh as evidence-only."""

import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import func, select

from app.models import CrawlJob, DiscoveredUrl, DiscoverySource, Domain, Store
from app.services import discovery, verification
from app.services.fetcher import SafeFetcher
from app.sources import Found

IP = "23.227.38.65"


def tag() -> str:
    return uuid.uuid4().hex[:8]


def test_seeded_source_configuration(db):
    cc = db.scalar(select(DiscoverySource).where(DiscoverySource.name == "common_crawl_shopify"))
    assert cc.source_type == "commoncrawl" and cc.configuration["host_suffixes"] == ["myshopify.com"]
    assert cc.configuration["backlog_rank"] == 2 and cc.configuration["max_blocks_per_run"] >= 95
    ct = db.scalar(select(DiscoverySource).where(DiscoverySource.name == "certificate_transparency"))
    assert ct.configuration["evidence_only"] is True and ct.configuration["verify_new_limit"] == 0
    assert "schedule_hours" not in ct.configuration  # no scheduled discovery runs


def test_provider_ref_is_recorded_and_refreshed(db):
    src = DiscoverySource(name=f"cc-{tag()}", source_type="commoncrawl", configuration={}, state={})
    db.add(src)
    db.commit()
    run = discovery.start_run(db, src)
    url = f"https://x{tag()}.myshopify.com/"
    discovery.ingest(db, src.id, run, [Found(url, datetime(2026, 10, 5, tzinfo=UTC), "CC-MAIN-2026-43")], 0, lambda j: None)
    row = db.scalar(select(DiscoveredUrl).where(DiscoveredUrl.normalized_url == url))
    assert row.source_ref == "CC-MAIN-2026-43"
    discovery.ingest(db, src.id, run, [Found(url, datetime(2026, 12, 1, tzinfo=UTC), "CC-MAIN-2026-49")], 0, lambda j: None)
    db.refresh(row)
    assert row.source_ref == "CC-MAIN-2026-49"  # latest index that saw it
    assert row.first_discovered_at.replace(tzinfo=UTC).date().isoformat() == "2026-10-05"  # earliest capture kept


def test_evidence_only_source_never_creates_candidates(db):
    src = DiscoverySource(name=f"ct-{tag()}", source_type="crtsh", configuration={"evidence_only": True}, state={})
    known = Domain(normalized_domain=f"known{tag()}.example")
    db.add_all([src, known])
    db.commit()
    run = discovery.start_run(db, src)
    unknown = f"unknown{tag()}.example"
    queued = []
    stats = discovery.ingest(db, src.id, run, [Found(f"https://{known.normalized_domain}/", ref="crt.sh:1"),
                                               Found(f"https://{unknown}/", ref="crt.sh:2")], 10, queued.append)  # fmt: skip
    assert stats["urls"] == 1 and stats["new_domains"] == 0 and queued == []
    assert db.scalar(select(Domain).where(Domain.normalized_domain == unknown)) is None
    assert db.scalar(select(DiscoveredUrl.source_ref).where(DiscoveredUrl.domain_id == known.id)) == "crt.sh:1"


def _verify(handler):
    return verification.verify("dormant.example", SafeFetcher(lambda h: [IP], httpx.MockTransport(handler)))


def test_password_protected_storefront_is_inactive_not_a_store():
    def handler(r):
        if r.url.path == "/robots.txt":
            return httpx.Response(404)
        if r.url.path == "/":
            return httpx.Response(302, headers={"location": "https://dormant.example/password"})
        return httpx.Response(200, text="<html><body><form action='/password'>Opening soon</form></body></html>",
                              headers={"content-type": "text/html"})  # fmt: skip

    r = _verify(handler)
    assert r.status == "inactive_storefront" and "password" in r.evidence["reason"]


def test_unavailable_storefront_402_is_inactive():
    r = _verify(lambda req: httpx.Response(404) if req.url.path == "/robots.txt" else httpx.Response(402, text="unavailable"))
    assert r.status == "inactive_storefront" and "402" in r.evidence["reason"]


def test_inactive_storefront_creates_no_store_and_counts_as_negative_for_existing(db):
    from app.services import monitoring

    d = Domain(normalized_domain=f"dorm{tag()}.myshopify.com")
    db.add(d)
    db.flush()
    job = CrawlJob(domain_id=d.id, target_domain=d.normalized_domain, job_type="verify", status="running")
    db.add(job)
    db.commit()
    verification.apply_result(db, job, verification.VerificationResult("inactive_storefront", 0.9, "resolved", {"reason": "x"}))
    assert db.scalar(select(func.count()).select_from(Store).where(Store.domain_id == d.id)) == 0
    assert monitoring.failure_kind("inactive_storefront", "resolved") == "negative"
