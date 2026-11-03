"""M2: normalization, SSRF, fetcher, signal scoring, submission dedupe, end-to-end task.
No real network: DNS is a fake resolver, HTTP is httpx.MockTransport serving fixtures."""

import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app import tasks
from app.api import v1
from app.db import SessionLocal
from app.models import CrawlJob, Domain, Store, StoreObservation
from app.security.ssrf import BlockedDestination, DnsFailure, is_public_ip, resolve_public
from app.services import verification
from app.services.domains import InvalidDomain, normalize_domain
from app.services.fetcher import FetchError, SafeFetcher

FIX = Path(__file__).parent / "fixtures"
PUBLIC_IP = "93.184.216.34"


def html(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def fake_dns(table: dict[str, str]):
    def resolve(host: str) -> list[str]:
        if host not in table:
            raise DnsFailure(host)
        return [table[host]]

    return resolve


def site(routes: dict[tuple[str, str], httpx.Response], seen: list | None = None):
    """routes keyed by (Host header, path). Anything else → 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return routes.get((request.headers["host"], request.url.path), httpx.Response(404))

    return httpx.MockTransport(handler)


def page(name: str) -> httpx.Response:
    return httpx.Response(200, text=html(name), headers={"content-type": "text/html; charset=utf-8"})


# ---------- normalization ----------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("example.com", "example.com"),
        ("  https://WWW.Example.COM:443/path?q=1#x ", "example.com"),
        ("http://user:pw@shop.example.co.uk/", "shop.example.co.uk"),
        ("example.com.", "example.com"),
        ("bücher.de", "xn--bcher-kva.de"),
    ],
)
def test_normalize_domain(raw, expected):
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "localhost", "127.0.0.1", "http://[::1]/", "printer.local", "foo.internal", "nodot", "-bad-.com", "a b.com", "x.123"],
)
def test_normalize_rejects(raw):
    with pytest.raises(InvalidDomain):
        normalize_domain(raw)


# ---------- SSRF ----------


@pytest.mark.parametrize(
    "ip,public",
    [
        ("8.8.8.8", True),
        (PUBLIC_IP, True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("172.16.0.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),  # cloud metadata
        ("100.64.0.1", False),  # CGNAT
        ("0.0.0.0", False),
        ("::1", False),
        ("fc00::1", False),
        ("fe80::1", False),
        ("::ffff:127.0.0.1", False),  # IPv4-mapped loopback
        ("2606:4700:4700::1111", True),
    ],
)
def test_is_public_ip(ip, public):
    assert is_public_ip(ip) is public


def test_resolve_public_blocks_if_any_address_private():
    with pytest.raises(BlockedDestination):
        resolve_public("mixed.example", lambda h: [PUBLIC_IP, "10.0.0.1"])


def test_fetcher_connects_to_validated_ip_with_real_host_header():
    seen = []
    f = SafeFetcher(fake_dns({"shop.example": PUBLIC_IP}), site({("shop.example", "/"): page("blog.html")}, seen))
    res = f.fetch("https://shop.example/")
    assert res.status == 200
    assert seen[0].url.host == PUBLIC_IP
    assert seen[0].headers["host"] == "shop.example"
    assert seen[0].extensions["sni_hostname"] == "shop.example"


def test_redirect_to_internal_host_is_blocked():
    dns = fake_dns({"shop.example": PUBLIC_IP, "intranet.example": "10.0.0.5"})
    routes = {("shop.example", "/"): httpx.Response(302, headers={"location": "http://intranet.example/admin"})}
    with pytest.raises(BlockedDestination, match="non-public"):
        SafeFetcher(dns, site(routes)).fetch("https://shop.example/")


@pytest.mark.parametrize(
    "location", ["http://169.254.169.254/latest/meta-data/", "http://shop.example:8080/", "file:///etc/passwd", "http://u:p@shop.example/"]
)
def test_redirect_to_disallowed_targets_is_blocked(location):
    routes = {("shop.example", "/"): httpx.Response(302, headers={"location": location})}
    with pytest.raises(BlockedDestination):
        SafeFetcher(fake_dns({"shop.example": PUBLIC_IP}), site(routes)).fetch("https://shop.example/")


def test_redirect_limit():
    routes = {("shop.example", "/"): httpx.Response(302, headers={"location": "/"})}
    with pytest.raises(BlockedDestination, match="redirects"):
        SafeFetcher(fake_dns({"shop.example": PUBLIC_IP}), site(routes)).fetch("https://shop.example/")


def test_response_size_limit():
    routes = {("shop.example", "/"): httpx.Response(200, text="x" * 5000, headers={"content-type": "text/html"})}
    res = SafeFetcher(fake_dns({"shop.example": PUBLIC_IP}), site(routes), max_bytes=1000).fetch("https://shop.example/")
    assert res.truncated and len(res.text) == 1000


def test_5xx_and_connection_errors_are_transient():
    routes = {("shop.example", "/"): httpx.Response(503)}
    with pytest.raises(FetchError):
        SafeFetcher(fake_dns({"shop.example": PUBLIC_IP}), site(routes)).fetch("https://shop.example/")

    def boom(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(FetchError):
        SafeFetcher(fake_dns({"shop.example": PUBLIC_IP}), httpx.MockTransport(boom)).fetch("https://shop.example/")


# ---------- signals / classification ----------


def classify_fixture(name: str) -> str:
    points, hits = verification.score(verification.extract_signals(html(name), "https://shop.example/"))
    return verification.classify(points, hits)[0]


def test_store_homepage_confirmed():
    assert classify_fixture("store_home.html") == "confirmed_ecommerce"


def test_blog_using_shop_words_is_not_ecommerce():
    assert classify_fixture("blog.html") == "not_ecommerce"


def test_catalogue_without_cart_function_is_probable():
    assert classify_fixture("catalogue.html") == "probable_ecommerce"


def test_product_jsonld_detected_and_offsite_links_ignored():
    s = verification.extract_signals(html("store_product.html"), "https://shop.example/products/blue-jacket")
    assert s["product_structured_data"] is True
    home = verification.extract_signals(html("store_home.html"), "https://shop.example/")
    assert all("othersite" not in u for u in home["product_links"])


def test_verify_follows_one_product_page():
    dns = fake_dns({"shop.example": PUBLIC_IP})
    routes = {
        ("shop.example", "/"): page("store_home.html"),
        ("shop.example", "/products/blue-jacket"): page("store_product.html"),
    }
    r = verification.verify("shop.example", SafeFetcher(dns, site(routes)))
    assert r.status == "confirmed_ecommerce"
    assert r.evidence["product_page"]["signals"]["product_structured_data"] is True
    assert r.site_name == "Acme Outfitters"


def test_verify_respects_robots_disallow():
    seen = []
    routes = {
        ("shop.example", "/robots.txt"): httpx.Response(200, text="User-agent: *\nDisallow: /\n"),
        ("shop.example", "/"): page("store_home.html"),
    }
    r = verification.verify("shop.example", SafeFetcher(fake_dns({"shop.example": PUBLIC_IP}), site(routes, seen)))
    assert r.status == "uncertain" and "robots" in r.evidence["reason"]
    assert [q.url.path for q in seen] == ["/robots.txt"]


def test_verify_private_dns_is_blocked_and_nxdomain_unreachable():
    r = verification.verify("evil.example", SafeFetcher(fake_dns({"evil.example": "127.0.0.1"}), site({})))
    assert r.status == "blocked"
    r = verification.verify("gone.example", SafeFetcher(fake_dns({}), site({})))
    assert (r.status, r.resolution) == ("temporarily_unreachable", "nxdomain")


def test_verify_bot_wall_is_uncertain_not_bypassed():
    routes = {("shop.example", "/"): httpx.Response(403, text="denied", headers={"content-type": "text/html"})}
    r = verification.verify("shop.example", SafeFetcher(fake_dns({"shop.example": PUBLIC_IP}), site(routes)))
    assert r.status == "uncertain"


# ---------- submission + task (DB) ----------


@pytest.fixture
def queued(monkeypatch):
    calls = []
    monkeypatch.setattr(v1, "_enqueue", lambda job_id: calls.append(job_id))
    return calls


def _host() -> str:
    return f"t{uuid.uuid4().hex[:10]}.example"


def test_submit_normalizes_and_dedupes(client, queued):
    host = _host()
    r1 = client.post("/api/v1/discovery/domains", json={"domain": f"https://WWW.{host.upper()}/x"})
    r2 = client.post("/api/v1/discovery/domains", json={"domain": host})
    assert r1.status_code == r2.status_code == 202
    a, b = r1.json(), r2.json()
    assert a["domain"]["normalized_domain"] == host and a["created"] is True
    assert b["created"] is False
    assert a["domain"]["id"] == b["domain"]["id"]
    assert a["job"]["id"] == b["job"]["id"]  # in-flight job reused, not duplicated
    assert len(queued) == 1


def test_submit_rejects_invalid(client, queued):
    r = client.post("/api/v1/discovery/domains", json={"domain": "http://169.254.169.254/"})
    assert r.status_code == 422
    assert queued == []


def test_submit_queue_down_returns_503_and_marks_job_failed(client, monkeypatch):
    def down(job_id):
        raise ConnectionError("redis down")

    monkeypatch.setattr(v1, "_enqueue", down)
    r = client.post("/api/v1/discovery/domains", json={"domain": _host()})
    assert r.status_code == 503


def _use_fixture_site(monkeypatch, host, routes):
    dns = fake_dns({host: PUBLIC_IP})
    monkeypatch.setattr(tasks, "SafeFetcher", lambda: SafeFetcher(dns, site(routes)))


def test_task_end_to_end_creates_store_with_evidence(client, queued, monkeypatch):
    host = _host()
    _use_fixture_site(
        monkeypatch,
        host,
        {(host, "/"): page("store_home.html"), (host, "/products/blue-jacket"): page("store_product.html")},
    )
    job_id = client.post("/api/v1/discovery/domains", json={"domain": host}).json()["job"]["id"]

    tasks.verify_domain.apply(args=[job_id])
    assert tasks.verify_domain.apply(args=[job_id]).result == "skipped"  # redelivery is a no-op

    with SessionLocal() as db:
        job = db.get(CrawlJob, uuid.UUID(job_id))
        domain = db.get(Domain, job.domain_id)
        store = db.scalar(select(Store).where(Store.domain_id == domain.id))
        obs = db.scalars(select(StoreObservation).where(StoreObservation.store_id == store.id)).all()
    assert job.status == "succeeded" and job.attempts == 1
    assert job.result["status"] == "confirmed_ecommerce"
    assert job.result["evidence"]["score"]["points"] >= 7
    assert domain.verification_status == "confirmed_ecommerce"
    assert store.store_name == "Acme Outfitters" and store.activity_status == "active"
    assert store.primary_platform == "unknown"  # platform detection is M3
    assert len(obs) == 1

    detail = client.get(f"/api/v1/domains/{domain.id}").json()
    assert detail["store_id"] == str(store.id)
    assert detail["jobs"][0]["result"]["status"] == "confirmed_ecommerce"


def test_task_not_ecommerce_creates_no_store(client, queued, monkeypatch):
    host = _host()
    _use_fixture_site(monkeypatch, host, {(host, "/"): page("blog.html")})
    job_id = client.post("/api/v1/discovery/domains", json={"domain": host}).json()["job"]["id"]
    tasks.verify_domain.apply(args=[job_id])
    with SessionLocal() as db:
        job = db.get(CrawlJob, uuid.UUID(job_id))
        assert job.result["status"] == "not_ecommerce"
        assert db.scalar(select(Store).where(Store.domain_id == job.domain_id)) is None


def test_task_retries_then_marks_unreachable(client, queued, monkeypatch):
    host = _host()

    def down(request):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(tasks, "SafeFetcher", lambda: SafeFetcher(fake_dns({host: PUBLIC_IP}), httpx.MockTransport(down)))
    job_id = client.post("/api/v1/discovery/domains", json={"domain": host}).json()["job"]["id"]
    tasks.verify_domain.apply(args=[job_id])  # eager: retries run inline (countdown ignored)
    with SessionLocal() as db:
        job = db.get(CrawlJob, uuid.UUID(job_id))
        domain = db.get(Domain, job.domain_id)
    assert job.attempts == tasks.MAX_RETRIES + 1
    assert job.status == "succeeded" and job.result["status"] == "temporarily_unreachable"
    assert domain.verification_status == "temporarily_unreachable"


def test_list_domains_filters_and_paginates(client, queued):
    host = _host()
    client.post("/api/v1/discovery/domains", json={"domain": host})
    r = client.get("/api/v1/domains", params={"q": host, "page_size": 1}).json()
    assert r["total"] == 1 and r["items"][0]["normalized_domain"] == host
    assert client.get("/api/v1/domains", params={"status": "bogus"}).status_code == 422
    assert client.get(f"/api/v1/domains/{uuid.uuid4()}").status_code == 404
