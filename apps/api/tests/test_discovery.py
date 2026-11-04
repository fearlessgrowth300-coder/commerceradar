"""M4: discovery sources → one deduplicated candidate pipeline.
No real network: fake DNS + httpx.MockTransport (with HTTP Range support for Common Crawl)."""

import gzip
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app import tasks
from app.api import v1
from app.db import SessionLocal
from app.models import CrawlJob, DiscoveredUrl, DiscoveryRun, DiscoverySource, Domain, Store, StoreObservation
from app.security.ssrf import DnsFailure
from app.services import discovery
from app.services.fetcher import SafeFetcher
from app.sources import Ctx, Found, commoncrawl, crawler, crtsh, urlscan

PUBLIC_IP = "93.184.216.34"


def fake_dns(hosts):
    def resolve(host):
        if host not in hosts:
            raise DnsFailure(f"no such host {host}")
        return [PUBLIC_IP]

    return resolve


def transport(handler):
    return httpx.MockTransport(handler)


def tag() -> str:
    return uuid.uuid4().hex[:8]


def make_source(db, source_type: str, config: dict) -> DiscoverySource:
    s = DiscoverySource(name=f"test-{source_type}-{tag()}", source_type=source_type, configuration=config, state={})
    db.add(s)
    db.commit()
    return s


# ---------- normalization + shared ingest (the acceptance criterion) ----------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://WWW.Shop-One.example/a?b=1#frag", ("https://shop-one.example/a?b=1", "shop-one.example")),
        ("shop-two.example", ("https://shop-two.example/", "shop-two.example")),
        ("http://shop-three.example:8080/x", ("http://shop-three.example/x", "shop-three.example")),
        ("ftp://files.example/", None),
        ("https://localhost/", None),
        ("https://10.0.0.1/", None),
    ],
)
def test_normalize_url(raw, expected):
    assert discovery.normalize_url(raw) == expected


def test_two_sources_same_domain_one_candidate_one_job(db):
    a = make_source(db, "crtsh", {})
    b = make_source(db, "urlscan", {})
    host = f"dup{tag()}.example"
    queued = []
    for src, url in ((a, f"https://{host}/"), (b, f"https://www.{host}/products/x"), (a, f"https://{host}/")):
        run = discovery.start_run(db, src) or db.scalars(select(DiscoveryRun).where(DiscoveryRun.source_id == src.id)).first()
        discovery.ingest(db, src.id, run, [Found(url)], verify_budget=10, enqueue=queued.append)

    domains = db.scalars(select(Domain).where(Domain.normalized_domain == host)).all()
    assert len(domains) == 1
    urls = db.scalars(select(DiscoveredUrl).where(DiscoveredUrl.hostname == host)).all()
    assert sorted((u.source_id == a.id, u.normalized_url) for u in urls) == [
        (False, f"https://{host}/products/x"),
        (True, f"https://{host}/"),
    ]
    assert all(u.domain_id == domains[0].id for u in urls)
    jobs = db.scalars(select(CrawlJob).where(CrawlJob.domain_id == domains[0].id)).all()
    assert len(jobs) == 1 and len(queued) == 1  # verified once, whichever source saw it first


def test_ingest_keeps_earliest_sighting(db):
    src = make_source(db, "crtsh", {})
    run = discovery.start_run(db, src)
    url = f"https://early{tag()}.example/"
    discovery.ingest(db, src.id, run, [Found(url, datetime(2026, 9, 1, tzinfo=UTC))], 0, lambda j: None)
    discovery.ingest(db, src.id, run, [Found(url, datetime(2021, 3, 5, tzinfo=UTC))], 0, lambda j: None)  # full history
    discovery.ingest(db, src.id, run, [Found(url, datetime(2026, 10, 1, tzinfo=UTC))], 0, lambda j: None)  # later, ignored
    row = db.scalar(select(DiscoveredUrl).where(DiscoveredUrl.normalized_url == url))
    db.refresh(row)
    assert row.first_discovered_at.replace(tzinfo=UTC).date().isoformat() == "2021-03-05"


def test_crtsh_keeps_earliest_certificate_per_host_and_uses_full_history(db):
    t = tag()
    seen_urls = []

    def handler(request):
        seen_urls.append(str(request.url))
        return httpx.Response(200, json=[
            {"id": 5, "name_value": f"h{t}.example", "not_before": "2026-09-01T00:00:00"},
            {"id": 2, "name_value": f"h{t}.example", "not_before": "2019-01-01T00:00:00"},
        ])

    ctx = Ctx(SafeFetcher(fake_dns({"crt.sh"}), transport(handler)), db)
    b = crtsh.CrtSh().fetch_batch({"queries": [f"%.{t}.example"], "seed_from_stores": False}, {}, {}, ctx)
    assert b.found[0].seen_at == datetime(2019, 1, 1, tzinfo=UTC)
    assert "exclude=expired" not in seen_urls[0]


def test_crtsh_oversized_query_is_skipped_not_fatal(db):
    ctx = Ctx(SafeFetcher(fake_dns({"crt.sh"}), transport(lambda r: httpx.Response(404))), db)
    b = crtsh.CrtSh().fetch_batch({"queries": ["%.a.example", "%.b.example"], "seed_from_stores": False}, {}, {}, ctx)
    assert b.found == [] and b.cursor == {"i": 1} and "skipped" in b.note


def test_ingest_respects_budget_and_skips_known_domains(db, client, monkeypatch):
    monkeypatch.setattr(v1, "_enqueue", lambda job_id: None)
    known = f"known{tag()}.example"
    client.post("/api/v1/discovery/domains", json={"domain": known})  # manual submission already verified it
    src = make_source(db, "crtsh", {})
    run = discovery.start_run(db, src)
    new = [f"n{i}{tag()}.example" for i in range(5)]
    queued = []
    stats = discovery.ingest(
        db, src.id, run, [Found(f"https://{h}/") for h in [known, *new]] + [Found("https://localhost/")], 3, queued.append
    )
    assert stats == {"urls": 6, "invalid": 1, "new_domains": 5, "queued": 3}
    assert len(queued) == 3
    known_jobs = db.scalar(select(func.count()).select_from(CrawlJob).where(CrawlJob.target_domain == known))
    assert known_jobs == 1  # only the manual one


# ---------- Common Crawl (ZipNum index read via Range requests) ----------


def cc_record(key: str, url: str, status: str = "200", mime: str = "text/html") -> str:
    return f'{key} 20261005120000 {json.dumps({"url": url, "status": status, "mime-detected": mime})}'


def build_cc_index(lines: list[str], per_block: int = 2) -> tuple[bytes, bytes]:
    cdx, cluster = b"", []
    for i in range(0, len(lines), per_block):
        block = gzip.compress(("\n".join(lines[i : i + per_block]) + "\n").encode())
        key = lines[i].split(" ", 2)
        cluster.append(f"{key[0]} {key[1]}\tcdx-00000.gz\t{len(cdx)}\t{len(block)}\t{i // per_block + 1}")
        cdx += block
    return ("\n".join(cluster) + "\n").encode(), cdx


def range_server(files: dict[str, bytes], seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        body = files.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        a, b = request.headers["range"].removeprefix("bytes=").split("-")
        a, b = int(a), min(int(b), len(body) - 1)
        if seen is not None:
            seen.append((request.url.path, a, b))
        if a >= len(body):
            return httpx.Response(416)
        return httpx.Response(206, content=body[a : b + 1], headers={"content-range": f"bytes {a}-{b}/{len(body)}"})

    return handler


CC_LINES = [
    cc_record("au,com,shop)/", "https://shop.com.au/"),
    cc_record("au,com,zz)/", "https://zz.com.au/"),
    cc_record("boutique,alpha)/", "https://alpha.boutique/"),
    cc_record("boutique,alpha)/p/1", "https://alpha.boutique/p/1"),
    cc_record("boutique,beta)/", "https://beta.boutique/", status="404"),
    cc_record("boutique,delta,www)/", "https://www.delta.boutique/"),
    cc_record("boutique,gamma)/logo.png", "https://gamma.boutique/logo.png", mime="image/png"),
    cc_record("boutiques,x)/", "https://x.boutiques/"),
    cc_record("com,example)/", "https://example.com/"),
    cc_record("com,zeta)/", "https://zeta.com/"),
]


def cc_ctx(db, monkeypatch, seen=None):
    cluster, cdx = build_cc_index(CC_LINES)
    base = "/cc-index/collections/CC-MAIN-2026-43/indexes/"
    files = {base + "cluster.idx": cluster, base + "cdx-00000.gz": cdx}
    monkeypatch.setattr(commoncrawl, "PROBE", 64)  # force a real binary search on the tiny index
    f = SafeFetcher(fake_dns({"data.commoncrawl.org"}), transport(range_server(files, seen)))
    return Ctx(f, db)


def test_surt_prefix():
    assert commoncrawl.surt_prefix("shop") == "shop,"
    assert commoncrawl.surt_prefix(".co.uk") == "uk,co,"


def test_commoncrawl_reads_only_matching_blocks_and_is_incremental(db, monkeypatch):
    seen = []
    ctx = cc_ctx(db, monkeypatch, seen)
    cfg = {"index": "CC-MAIN-2026-43", "host_suffixes": ["boutique"], "blocks_per_batch": 1}
    state, cursor, urls = {}, {}, []
    for _ in range(10):
        batch = commoncrawl.CommonCrawl().fetch_batch(cfg, cursor, state, ctx)
        urls += [f.url for f in batch.found]
        if batch.cursor is None:
            break
        cursor = batch.cursor
    assert sorted(urls) == ["https://alpha.boutique/", "https://www.delta.boutique/"]  # 404 + image skipped, 1 per host
    assert state["CC-MAIN-2026-43:boutique"] == "done"
    blocks = [r for r in seen if r[0].endswith("cdx-00000.gz")]
    assert 2 <= len(blocks) <= 4  # never the .com blocks at the end
    # next run: nothing re-read
    seen.clear()
    batch = commoncrawl.CommonCrawl().fetch_batch(cfg, {}, state, ctx)
    assert batch.cursor is None and not batch.found and not seen


def test_commoncrawl_rejects_bad_config(db, monkeypatch):
    with pytest.raises(Exception, match="config needs"):
        commoncrawl.CommonCrawl().fetch_batch({"index": "../etc", "host_suffixes": ["shop"]}, {}, {}, cc_ctx(db, monkeypatch))


# ---------- Certificate Transparency (crt.sh) ----------


def test_crt_hostnames_filter():
    assert crtsh.hostnames("*.Shop.example\nmail.shop.example\nwww.shop.example\nfoo@bar.example") == [
        "shop.example",
        "www.shop.example",
    ]


def crt_ctx(db, entries):
    def handler(request):
        assert request.url.params["output"] == "json"
        return httpx.Response(200, json=entries)

    return Ctx(SafeFetcher(fake_dns({"crt.sh"}), transport(handler)), db)


def test_crtsh_incremental_by_certificate_id(db):
    t = tag()
    entries = [
        {"id": 10, "name_value": f"a{t}.example\nwww.a{t}.example", "not_before": "2026-10-01T00:00:00"},
        {"id": 11, "name_value": f"*.b{t}.example", "not_before": "2026-10-02T00:00:00"},
    ]
    cfg, state = {"queries": [f"%.{t}.example"], "seed_from_stores": False}, {}
    src = crtsh.CrtSh()
    b1 = src.fetch_batch(cfg, {}, state, crt_ctx(db, entries))
    assert sorted(f.url for f in b1.found) == [f"https://a{t}.example/", f"https://b{t}.example/", f"https://www.a{t}.example/"]
    assert b1.found[0].seen_at == datetime(2026, 10, 1, tzinfo=UTC)
    assert src.fetch_batch(cfg, b1.cursor, state, crt_ctx(db, entries)).cursor is None
    again = src.fetch_batch(cfg, {}, state, crt_ctx(db, entries))
    assert again.found == []  # same certificates next run ⇒ nothing new


def test_crtsh_seeds_from_confirmed_stores(db):
    host = f"seed{tag()}.example"
    d = Domain(normalized_domain=host)
    db.add(d)
    db.flush()
    db.add(Store(domain_id=d.id, ecommerce_status="confirmed_ecommerce"))
    db.commit()
    assert f"%.{host}" in crtsh.CrtSh().queries({"max_store_queries": 100000}, Ctx(None, db))


# ---------- urlscan ----------


def test_urlscan_pages_and_stops_at_previous_run(db):
    def result(host, t, sort):
        return {"page": {"url": f"https://{host}/"}, "task": {"time": t}, "sort": sort}

    page1 = {"results": [result("s1.example", "2026-11-03T10:00:00Z", [3, "c"]),
                         result("s2.example", "2026-11-03T09:00:00Z", [2, "b"])], "has_more": True}  # fmt: skip
    page2 = {"results": [result("s3.example", "2026-11-02T09:00:00Z", [1, "a"])], "has_more": False}
    calls = []

    def handler(request):
        calls.append(dict(request.url.params))
        return httpx.Response(200, json=page2 if "search_after" in request.url.params else page1)

    ctx = Ctx(SafeFetcher(fake_dns({"urlscan.io"}), transport(handler)), db)
    cfg, state, src = {"query": "domain:myshopify.com", "page_size": 2}, {}, urlscan.UrlScan()
    b1 = src.fetch_batch(cfg, {}, state, ctx)
    b2 = src.fetch_batch(cfg, b1.cursor, state, ctx)
    assert [f.url for f in b1.found + b2.found] == ["https://s1.example/", "https://s2.example/", "https://s3.example/"]
    assert calls[1]["search_after"] == "2,b" and b2.cursor is None  # sort key of page 1's last result
    assert state["since"] == "2026-11-03T10:00:00Z"
    # next run: page1 again, but everything is at/older than `since`
    b3 = src.fetch_batch(cfg, {}, state, ctx)
    assert b3.found == [] and b3.cursor is None


def test_urlscan_rate_limit_is_retryable(db):
    ctx = Ctx(SafeFetcher(fake_dns({"urlscan.io"}), transport(lambda r: httpx.Response(429))), db)
    from app.services.fetcher import FetchError

    with pytest.raises(FetchError):
        urlscan.UrlScan().fetch_batch({"query": "x"}, {}, {}, ctx)


# ---------- own crawler ----------


def test_crawler_rules():
    assert crawler.skip_host("www.instagram.com") and crawler.skip_host("instagram.com")
    assert not crawler.skip_host("shop.brand.example") and not crawler.skip_host("t.example")
    assert crawler.crawlable("https://www.s.example/collections/a", "s.example")
    for trap in ("https://s.example/cart", "https://s.example/a/b/c/d/e", "https://s.example/x.pdf",
                 "https://s.example/p?" + "f=1&" * 20, "https://other.example/"):  # fmt: skip
        assert not crawler.crawlable(trap, "s.example"), trap


def test_crawler_follows_allowed_links_and_collects_external_hosts(db):
    host = f"store{tag()}.example"
    pages = {
        "/robots.txt": "User-agent: *\nDisallow: /private\n",
        "/": f"""<a href="/products/a">A</a><a href="/private/x">P</a><a href="/cart">C</a>
                <a href="https://partner{host}/x">partner</a><a href="https://www.instagram.com/b">ig</a>
                <a href="mailto:hi@{host}">mail</a>""",
        "/products/a": '<a href="https://another.example/">another</a>',
        "/private/x": '<a href="https://secret.example/">never</a>',
    }
    fetched = []

    def handler(request):
        fetched.append(request.url.path)
        body = pages.get(request.url.path)
        ctype = "text/plain" if request.url.path == "/robots.txt" else "text/html"
        return httpx.Response(200, text=body, headers={"content-type": ctype}) if body else httpx.Response(404)

    ctx = Ctx(SafeFetcher(fake_dns({host}), transport(handler)), db)
    external, crawled = crawler.LinkCrawler().crawl(host, {"page_delay_seconds": 0}, ctx)
    assert sorted(external) == ["another.example", f"partner{host}"]
    assert "/private/x" not in fetched and "/cart" not in fetched
    assert crawled == [f"https://{host}/", f"https://{host}/products/a"]


def test_crawler_batch_records_observation_and_skips_recently_crawled(db):
    host = f"crawlme{tag()}.example"
    d = Domain(normalized_domain=host)
    db.add(d)
    db.flush()
    store = Store(domain_id=d.id, ecommerce_status="confirmed_ecommerce")
    db.add(store)
    db.commit()

    def handler(request):
        return httpx.Response(200, text='<a href="https://friend.example/">f</a>', headers={"content-type": "text/html"})

    ctx = Ctx(SafeFetcher(fake_dns({host}), transport(handler)), db)  # other stores: DNS fails fast
    cfg = {"page_delay_seconds": 0, "max_pages_per_site": 1, "max_sites_per_run": 100000}
    cursor, found = {}, []
    while cursor is not None:
        b = crawler.LinkCrawler().fetch_batch(cfg, cursor, {}, ctx)
        found += [f.url for f in b.found]
        cursor = b.cursor
    db.commit()
    assert "https://friend.example/" in found
    obs = db.scalars(select(StoreObservation).where(StoreObservation.store_id == store.id)).all()
    assert [o.observation_type for o in obs] == ["link_crawl"]
    again = crawler.LinkCrawler().fetch_batch(cfg, {}, {}, ctx)
    assert again.cursor is None  # every store crawled within recrawl_days


# ---------- run task: chaining, idempotency, retries, resume, API ----------


@pytest.fixture
def chain(monkeypatch):
    nexts, verifies = [], []
    monkeypatch.setattr(tasks.run_discovery_batch, "apply_async", lambda args, countdown: nexts.append(args))
    monkeypatch.setattr(tasks, "_enqueue_verify", verifies.append)
    return nexts, verifies


def use_handler(monkeypatch, hosts, handler):
    monkeypatch.setattr(tasks, "SafeFetcher", lambda **kw: SafeFetcher(fake_dns(hosts), transport(handler), **kw))


def test_run_task_end_to_end(db, chain, monkeypatch):
    nexts, verifies = chain
    t = tag()
    src = make_source(db, "crtsh", {"queries": [f"%.{t}.example"], "seed_from_stores": False, "verify_new_limit": 1})
    use_handler(monkeypatch, {"crt.sh"}, lambda r: httpx.Response(200, json=[
        {"id": 1, "name_value": f"one{t}.example"}, {"id": 2, "name_value": f"two{t}.example"}]))  # fmt: skip
    run = discovery.launch_run(db, src, lambda rid, seq: None)
    with pytest.raises(discovery.RunConflict):
        discovery.launch_run(db, src, lambda rid, seq: None)  # one active run per source

    assert tasks.run_discovery_batch.apply(args=[str(run.id), 0]).result == "continued"
    assert nexts == [[str(run.id), 1]]
    assert tasks.run_discovery_batch.apply(args=[str(run.id), 0]).result == "skipped"  # stale/duplicate message
    assert tasks.run_discovery_batch.apply(args=[str(run.id), 1]).result == "succeeded"

    with SessionLocal() as s:
        r = s.get(DiscoveryRun, run.id)
        src_after = s.get(DiscoverySource, src.id)
        n_domains = s.scalar(select(func.count()).select_from(Domain).where(Domain.normalized_domain.like(f"%{t}.example")))
    assert r.status == "succeeded" and r.records_found == 2 and r.records_processed == 2
    assert r.cursor["verify_queued"] == 1 and len(verifies) == 1  # verify_new_limit honoured
    assert src_after.state == {f"%.{t}.example": 2}
    assert n_domains == 2  # both stored as candidates, only one queued now


def test_claimed_batch_is_not_run_twice(db, chain):
    src = make_source(db, "crtsh", {"queries": [], "seed_from_stores": False})
    run = discovery.start_run(db, src)
    run.cursor = {"seq": 0, "claim": [0, datetime.now(UTC).isoformat()]}
    db.commit()
    assert tasks.run_discovery_batch.apply(args=[str(run.id), 0]).result == "skipped"
    run.cursor = {"seq": 0, "claim": [0, (datetime.now(UTC) - timedelta(hours=1)).isoformat()]}  # crashed worker
    db.commit()
    assert tasks.run_discovery_batch.apply(args=[str(run.id), 0]).result == "succeeded"


def test_rate_limited_source_retries_then_fails_and_resumes(db, chain, monkeypatch):
    src = make_source(db, "crtsh", {"queries": ["%.x.example"], "seed_from_stores": False})
    hits = []
    use_handler(monkeypatch, {"crt.sh"}, lambda r: hits.append(1) or httpx.Response(429))
    run = discovery.launch_run(db, src, lambda rid, seq: None)
    tasks.run_discovery_batch.apply(args=[str(run.id), 0])  # eager: retries run inline
    assert len(hits) == tasks.DISCOVERY_RETRIES + 1
    db.refresh(run)
    assert run.status == "failed" and "gave up" in run.error_summary and run.cursor["seq"] == 0

    use_handler(monkeypatch, {"crt.sh"}, lambda r: httpx.Response(200, json=[]))
    resumed = []
    discovery.resume_run(db, run, lambda rid, seq: resumed.append(seq))
    assert run.status == "queued" and resumed == [0]
    assert tasks.run_discovery_batch.apply(args=[str(run.id), 0]).result == "continued"


def test_permanent_source_error_fails_run(db, chain, monkeypatch):
    src = make_source(db, "crtsh", {"queries": ["%.y.example"], "seed_from_stores": False})
    use_handler(monkeypatch, {"crt.sh"}, lambda r: httpx.Response(200, text="<html>oops</html>"))
    run = discovery.launch_run(db, src, lambda rid, seq: None)
    assert tasks.run_discovery_batch.apply(args=[str(run.id), 0]).result == "failed"
    db.refresh(run)
    assert "non-JSON" in run.error_summary


def test_sources_api(client, monkeypatch):
    started = []
    monkeypatch.setattr(v1, "_enqueue_run", lambda rid, seq: started.append((rid, seq)))
    sources = {s["name"]: s for s in client.get("/api/v1/discovery/sources").json()}
    assert {"common_crawl", "certificate_transparency", "commerceradar_crawler", "urlscan"} <= set(sources)

    sid = sources["urlscan"]["id"]
    r = client.post(f"/api/v1/discovery/sources/{sid}/runs")
    assert r.status_code == 202 and r.json()["status"] == "queued" and len(started) == 1
    assert client.post(f"/api/v1/discovery/sources/{sid}/runs").status_code == 409
    runs = client.get("/api/v1/discovery/runs", params={"source_id": sid}).json()
    assert runs[0]["id"] == r.json()["id"]
    assert client.post(f"/api/v1/discovery/runs/{runs[0]['id']}/resume").status_code == 409  # still active
    assert client.post(f"/api/v1/discovery/sources/{uuid.uuid4()}/runs").status_code == 404
    with SessionLocal() as s:  # leave no active run behind for other tests
        s.get(DiscoveryRun, uuid.UUID(runs[0]["id"])).status = "cancelled"
        s.commit()
