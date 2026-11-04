"""M10 phase 2: store identity — aliases without duplicates, and without false merges.
No real network: fake DNS (per-host IPs) + httpx.MockTransport."""

import uuid
from pathlib import Path

import httpx
from sqlalchemy import func, select

from app.models import CrawlJob, Domain, Store, StoreDomain
from app.security.ssrf import DnsFailure
from app.services import identity, verification
from app.services.fetcher import SafeFetcher

SHOPIFY_IP = "23.227.38.65"  # Shopify's documented custom-domain A record
OTHER_IP = "93.184.216.34"
HOME = (Path(__file__).parent / "fixtures" / "store_home.html").read_text(encoding="utf-8")


def tag() -> str:
    return uuid.uuid4().hex[:8]


def page(shop: str | None = None, cookies: bool = False) -> httpx.Response:
    html = HOME.replace("</body>", f'<script>Shopify.shop = "{shop}";</script></body>') if shop else HOME
    headers = [("content-type", "text/html")] + ([("set-cookie", "_shopify_y=x; Path=/")] if cookies else [])
    return httpx.Response(200, text=html, headers=headers)


class Web:
    """host → (ip, response factory)."""

    def __init__(self):
        self.sites: dict[str, tuple[str, callable]] = {}

    def add(self, host, ip, resp):
        self.sites[host] = (ip, resp)

    def fetcher(self) -> SafeFetcher:
        def dns(h):
            h = h.removeprefix("www.")
            if h not in self.sites:
                raise DnsFailure(h)
            return [self.sites[h][0]]

        def handler(request):
            h = request.headers["host"].removeprefix("www.")
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return self.sites[h][1]() if h in self.sites else httpx.Response(404)

        return SafeFetcher(dns, httpx.MockTransport(handler))


def check(db, web: Web, host: str) -> Store | None:
    d = db.scalar(select(Domain).where(Domain.normalized_domain == host))
    if d is None:
        d = Domain(normalized_domain=host)
        db.add(d)
        db.flush()
    job = CrawlJob(domain_id=d.id, target_domain=host, job_type="verify", status="running")
    db.add(job)
    db.commit()
    verification.apply_result(db, job, verification.verify(host, web.fetcher()))
    link = identity.current_link(db, d.id)
    return db.get(Store, link.store_id) if link else None


def links(db, store) -> dict[str, tuple[str, str, float]]:
    q = (select(Domain.normalized_domain, StoreDomain.role, StoreDomain.alias_type, StoreDomain.confidence)
         .join(Domain, Domain.id == StoreDomain.domain_id).where(StoreDomain.store_id == store.id, StoreDomain.valid_to.is_(None)))  # fmt: skip
    return {d: (r, t, c) for d, r, t, c in db.execute(q)}


def live_stores_with_key(db, key):
    return db.scalar(select(func.count()).select_from(Store).where(Store.storefront_key == key, Store.merged_into_store_id.is_(None)))


def test_market_domains_of_one_shopify_store_are_aliases(db):
    t, web = tag(), Web()
    shop = f"brand-{t}.myshopify.com"
    for tld in ("de", "fr"):
        web.add(f"brand{t}.{tld}", SHOPIFY_IP, lambda: page(shop))
    a = check(db, web, f"brand{t}.de")
    b = check(db, web, f"brand{t}.fr")
    assert a.id == b.id and a.storefront_key == f"shopify:{shop}"
    assert links(db, a)[f"brand{t}.fr"] == ("alias", "storefront_id", 0.97)
    assert live_stores_with_key(db, f"shopify:{shop}") == 1


def test_copied_storefront_html_is_not_merged(db):
    """Measured on production: spam domains carried a real store's Shopify.shop id in copied HTML."""
    t, web = tag(), Web()
    shop = f"real-{t}.myshopify.com"
    web.add(f"real{t}.com", SHOPIFY_IP, lambda: page(shop))
    web.add(f"clone{t}.net", OTHER_IP, lambda: page(shop))  # not served by Shopify, no Shopify cookies
    real = check(db, web, f"real{t}.com")
    clone = check(db, web, f"clone{t}.net")
    assert real.id != clone.id and clone.storefront_key is None
    assert f"clone{t}.net" not in links(db, real)


def test_cookie_counts_as_shopify_serving_evidence(db):
    t, web = tag(), Web()
    shop = f"ck-{t}.myshopify.com"
    web.add(f"one{t}.com", OTHER_IP, lambda: page(shop, cookies=True))  # e.g. behind a CDN, but Shopify sets its cookies
    web.add(f"two{t}.com", OTHER_IP, lambda: page(shop, cookies=True))
    assert check(db, web, f"one{t}.com").id == check(db, web, f"two{t}.com").id


def test_myshopify_host_redirecting_to_custom_domain(db):
    t, web = tag(), Web()
    shop = f"shop{t}.myshopify.com"
    web.add(shop, SHOPIFY_IP, lambda: httpx.Response(301, headers={"location": f"https://brand{t}.com/"}))
    web.add(f"brand{t}.com", SHOPIFY_IP, lambda: page(shop))
    s = check(db, web, shop)
    assert db.get(Domain, s.domain_id).normalized_domain == f"brand{t}.com"  # primary = where the store lives
    assert links(db, s)[shop] == ("alias", "platform_hosted", 0.95)
    assert check(db, web, f"brand{t}.com").id == s.id  # verifying the custom domain later: same store


def test_platform_hosted_primary_is_promoted_when_custom_domain_appears(db):
    t, web = tag(), Web()
    shop = f"p{t}.myshopify.com"
    web.add(shop, SHOPIFY_IP, lambda: page(shop))  # store first seen on its myshopify host
    s = check(db, web, shop)
    assert db.get(Domain, s.domain_id).normalized_domain == shop
    web.add(f"custom{t}.com", SHOPIFY_IP, lambda: page(shop))
    assert check(db, web, f"custom{t}.com").id == s.id
    db.refresh(s)
    assert db.get(Domain, s.domain_id).normalized_domain == f"custom{t}.com"
    current = links(db, s)
    assert current[f"custom{t}.com"][0] == "primary" and current[shop][:2] == ("alias", "platform_hosted")
    closed = db.scalar(select(func.count()).select_from(StoreDomain).where(StoreDomain.store_id == s.id, StoreDomain.valid_to.is_not(None)))
    assert closed >= 1  # previous primary link kept as history


def test_same_site_redirect_is_an_alias(db):
    t, web = tag(), Web()
    web.add(f"shop.brand{t}.com", OTHER_IP, lambda: httpx.Response(301, headers={"location": f"https://brand{t}.com/"}))
    web.add(f"brand{t}.com", OTHER_IP, page)
    s = check(db, web, f"shop.brand{t}.com")
    assert links(db, s)[f"shop.brand{t}.com"] == ("alias", "redirect_same_site", 0.9)
    assert db.get(Domain, s.domain_id).normalized_domain == f"brand{t}.com"


def test_weak_cross_site_redirect_never_moves_an_existing_store(db):
    t, web = tag(), Web()
    web.add(f"old{t}.com", OTHER_IP, page)
    s = check(db, web, f"old{t}.com")
    web.add(f"old{t}.com", OTHER_IP, lambda: httpx.Response(301, headers={"location": f"https://other{t}.org/"}))
    web.add(f"other{t}.org", OTHER_IP, page)
    again = check(db, web, f"old{t}.com")
    assert again.id == s.id and db.get(Domain, s.domain_id).normalized_domain == f"old{t}.com"
    assert links(db, s)[f"other{t}.org"] == ("suggested", "redirect", 0.8)
    assert db.scalar(select(Store.id).where(Store.domain_id == db.scalar(select(Domain.id).where(Domain.normalized_domain == f"other{t}.org")))) is None


def test_redirect_to_marketplace_is_not_an_identity(db):
    t, web = tag(), Web()
    web.add(f"seller{t}.com", OTHER_IP, lambda: httpx.Response(302, headers={"location": "https://www.etsy.com/shop/x"}))
    web.add("etsy.com", OTHER_IP, page)
    s = check(db, web, f"seller{t}.com")
    assert db.get(Domain, s.domain_id).normalized_domain == f"seller{t}.com"


def test_existing_duplicate_stores_are_folded_on_strong_evidence(db, client):
    t, web = tag(), Web()
    shop = f"dup-{t}.myshopify.com"
    web.add(f"a{t}.com", SHOPIFY_IP, lambda: page(shop))
    first = check(db, web, f"a{t}.com")
    # a legacy duplicate created before identity links existed
    d = Domain(normalized_domain=f"b{t}.com", verification_status="confirmed_ecommerce")
    db.add(d)
    db.flush()
    legacy = Store(domain_id=d.id, ecommerce_status="confirmed_ecommerce", consecutive_failures=0)
    db.add(legacy)
    db.commit()
    web.add(f"b{t}.com", SHOPIFY_IP, lambda: page(shop))
    assert check(db, web, f"b{t}.com").id == first.id
    db.refresh(legacy)
    assert legacy.merged_into_store_id == first.id
    assert client.get(f"/api/v1/stores/{legacy.id}").status_code == 404
    assert all(r["id"] != str(legacy.id) for r in client.get("/api/v1/stores", params={"q": f"b{t}"}).json()["items"])
    detail = client.get(f"/api/v1/stores/{first.id}").json()
    assert {x["domain"] for x in detail["domains"] if x["current"]} >= {f"a{t}.com", f"b{t}.com"}


def test_reverification_is_idempotent(db):
    t, web = tag(), Web()
    shop = f"id-{t}.myshopify.com"
    web.add(f"x{t}.com", SHOPIFY_IP, lambda: page(shop))
    web.add(f"y{t}.com", SHOPIFY_IP, lambda: page(shop))
    s = check(db, web, f"x{t}.com")
    check(db, web, f"y{t}.com")
    before = db.scalar(select(func.count()).select_from(StoreDomain).where(StoreDomain.store_id == s.id))
    for _ in range(3):
        check(db, web, f"x{t}.com")
        check(db, web, f"y{t}.com")
    assert db.scalar(select(func.count()).select_from(StoreDomain).where(StoreDomain.store_id == s.id)) == before
    primaries = db.scalar(select(func.count()).select_from(StoreDomain).where(
        StoreDomain.store_id == s.id, StoreDomain.role == "primary", StoreDomain.valid_to.is_(None)))  # fmt: skip
    assert primaries == 1


def test_helpers():
    assert identity.registrable("shop.brand.co.uk") == "brand.co.uk"
    assert identity.registrable("a.b.example.com") == "example.com"
    assert identity.platform_hosted("x.myshopify.com") and not identity.platform_hosted("myshopify.com.evil.net")
    assert identity.shopify_served({"ip": SHOPIFY_IP}) and not identity.shopify_served({"ip": OTHER_IP})
    assert identity.storefront_key({"shopify_shop": "a.myshopify.com"}, {"cookies": ["cart_currency"]}) == "shopify:a.myshopify.com"
    assert identity.storefront_key({"shopify_shop": "a.myshopify.com"}, {"ip": OTHER_IP}) is None
