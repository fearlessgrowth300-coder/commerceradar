"""M5: product extraction from public pages (JSON-LD / OpenGraph), persistence, change history.
No real network: fake DNS + httpx.MockTransport."""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from app import tasks
from app.api import v1
from app.models import CrawlJob, Domain, Product, ProductObservation, Store, StoreObservation
from app.security.ssrf import DnsFailure
from app.services import products
from app.services.fetcher import SafeFetcher


def ld(obj) -> str:
    return f'<html><head><script type="application/ld+json">{json.dumps(obj)}</script></head><body></body></html>'


PRODUCT = {
    "@context": "https://schema.org",
    "@type": "Product",
    "name": "  Blue   Jacket ",
    "description": "Warm.",
    "image": ["/img/blue.jpg", "/img/blue2.jpg"],
    "sku": "BJ-1",
    "category": "Outerwear",
    "offers": {"@type": "Offer", "price": "1,299.00", "priceCurrency": "usd", "availability": "https://schema.org/InStock"},
}


# ---------- pure extraction ----------


def test_jsonld_product_fields():
    p = products.extract_product(ld(PRODUCT), "https://s.example/products/blue")
    assert p["title"] == "Blue Jacket" and p["description"] == "Warm."
    assert p["current_price"] == Decimal("1299.00") and p["currency"] == "USD" and p["availability"] == "in_stock"
    assert p["image_url"] == "https://s.example/img/blue.jpg" and p["sku"] == "BJ-1" and p["category"] == "Outerwear"
    assert p["method"] == "json-ld"


def test_jsonld_graph_productgroup_variants_and_aggregate():
    graph = {"@graph": [{"@type": "WebSite", "name": "S"},
                        {"@type": ["ProductGroup"], "name": "Tee",
                         "hasVariant": [{"@type": "Product", "offers": [{"@type": "Offer", "price": 12.5, "priceCurrency": "EUR",
                                                                          "availability": "OutOfStock"}]}]}]}  # fmt: skip
    p = products.extract_product(ld(graph), "https://s.example/p/tee")
    assert (p["title"], p["current_price"], p["currency"], p["availability"]) == ("Tee", Decimal("12.50"), "EUR", "out_of_stock")
    agg = {**PRODUCT, "offers": {"@type": "AggregateOffer", "lowPrice": "10", "highPrice": "20", "priceCurrency": "GBP"}}
    a = products.extract_product(ld(agg), "https://s.example/p/x")
    assert a["current_price"] == Decimal("10.00") and a["price_is_lowest_of_range"] is True


@pytest.mark.parametrize(
    "raw,expected",
    [("19.99", Decimal("19.99")), ("12,50", Decimal("12.50")), (7, Decimal("7.00")), ("free", None), ("-", None),
     ("", None), (None, None), (True, None), ("NaN", None), ("1e99", None)],
)  # fmt: skip
def test_price_parsing_never_guesses(raw, expected):
    assert products._price(raw) == expected


def test_missing_values_stay_missing():
    p = products.extract_product(ld({"@type": "Product", "name": "No price", "offers": {"priceCurrency": "dollars"}}), "https://s.example/p/a")
    assert p["title"] == "No price" and p["current_price"] is None and p["currency"] is None and p["availability"] is None


def test_opengraph_fallback_and_non_product_page():
    og = """<html><head><meta property="og:type" content="product"><meta property="og:title" content="Mug">
            <meta property="product:price:amount" content="9.00"><meta property="product:price:currency" content="cad">
            <meta property="og:image" content="//cdn.s.example/mug.png"></head></html>"""
    p = products.extract_product(og, "https://s.example/products/mug")
    assert (p["method"], p["title"], p["current_price"], p["currency"]) == ("opengraph", "Mug", Decimal("9.00"), "CAD")
    assert p["image_url"] == "https://cdn.s.example/mug.png"
    assert products.extract_product("<html><title>Blog</title></html>", "https://s.example/") is None
    assert products.extract_product(ld({"@type": "Organization", "name": "S"}), "https://s.example/") is None
    assert products.extract_product('<script type="application/ld+json">{broken</script>', "https://s.example/") is None


def test_sitemap_parsing():
    index = """<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://s.example/sitemap_pages_1.xml</loc></sitemap>
      <sitemap><loc> https://s.example/sitemap_products_1.xml </loc></sitemap></sitemapindex>"""
    assert products.sitemap_urls(index) == (
        ["https://s.example/sitemap_pages_1.xml", "https://s.example/sitemap_products_1.xml"], [])
    urlset = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://s.example/products/a</loc></url></urlset>'
    assert products.sitemap_urls(urlset) == ([], ["https://s.example/products/a"])
    assert products.sitemap_urls("<html>not xml") == ([], [])


# ---------- store extraction end to end ----------


def make_store(db, host: str, status: str = "confirmed_ecommerce") -> Store:
    d = Domain(normalized_domain=host)
    db.add(d)
    db.flush()
    s = Store(domain_id=d.id, ecommerce_status=status, canonical_url=f"https://{host}/")
    db.add(s)
    db.commit()
    return s


class Site:
    """Mutable fake store so a second pass can see price changes / removals."""

    def __init__(self, host: str):
        self.host = host
        self.prices = {"a": "10.00", "b": "20.00", "secret": "1.00"}
        self.gone: set[str] = set()
        self.fetched: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.fetched.append(path)
        h = self.host
        if path == "/robots.txt":
            return httpx.Response(200, text=f"User-agent: *\nDisallow: /products/secret\nSitemap: https://{h}/sitemap.xml\n")
        if path == "/sitemap.xml":
            return httpx.Response(200, text=f'<sitemapindex><sitemap><loc>https://{h}/sitemap_blog.xml</loc></sitemap>'
                                            f'<sitemap><loc>https://{h}/sitemap_products_1.xml</loc></sitemap></sitemapindex>')  # fmt: skip
        if path == "/sitemap_products_1.xml":
            locs = "".join(f"<url><loc>https://{h}/products/{k}</loc></url>" for k in self.prices)
            return httpx.Response(200, text=f"<urlset>{locs}<url><loc>https://{h}/about</loc></url></urlset>")
        if path == "/sitemap_blog.xml":
            return httpx.Response(200, text="<urlset></urlset>")
        slug = path.rsplit("/", 1)[-1]
        if path.startswith("/products/") and slug in self.prices:
            if slug in self.gone:
                return httpx.Response(404)
            body = ld({"@type": "Product", "name": slug.upper(), "offers": {"price": self.prices[slug], "priceCurrency": "USD"}})
            return httpx.Response(200, text=body, headers={"content-type": "text/html"})
        if path == "/":
            return httpx.Response(200, text="<html><body>home</body></html>", headers={"content-type": "text/html"})
        return httpx.Response(404)

    def fetcher(self) -> SafeFetcher:
        def dns(host):
            if host != self.host:
                raise DnsFailure(host)
            return ["93.184.216.34"]

        return SafeFetcher(dns, httpx.MockTransport(self.handler))


def history(db, product_id):
    q = select(ProductObservation).where(ProductObservation.product_id == product_id).order_by(ProductObservation.observed_at)
    return [(o.change_type, o.price) for o in db.scalars(q)]


def test_extract_store_saves_tracks_changes_and_removals(db):
    host = f"p{uuid.uuid4().hex[:8]}.example"
    store = make_store(db, host)
    site = Site(host)

    s1 = products.extract_store(db, store, site.fetcher(), max_pages=10, delay=0)
    assert (s1.new, s1.extracted, s1.disallowed) == (2, 2, 1)
    assert "/products/secret" not in site.fetched  # robots.txt respected
    assert site.fetched.index("/sitemap_products_1.xml") < site.fetched.index("/sitemap_blog.xml")  # product sitemap first
    rows = {p.product_url.rsplit("/", 1)[-1]: p for p in db.scalars(select(Product).where(Product.store_id == store.id))}
    assert set(rows) == {"a", "b"} and rows["a"].current_price == Decimal("10.00") and rows["a"].title == "A"

    site.prices["a"] = "12.00"
    site.gone.add("b")
    s2 = products.extract_store(db, store, site.fetcher(), max_pages=10, delay=0)
    assert (s2.new, s2.changed, s2.removed) == (0, 1, 1)
    db.refresh(rows["a"]), db.refresh(rows["b"])
    assert rows["a"].current_price == Decimal("12.00") and rows["b"].status == "removed"
    assert history(db, rows["a"].id) == [("new", Decimal("10.00")), ("changed", Decimal("12.00"))]
    assert history(db, rows["b"].id) == [("new", Decimal("20.00")), ("removed", Decimal("20.00"))]  # kept, not deleted

    s3 = products.extract_store(db, store, site.fetcher(), max_pages=10, delay=0)
    assert (s3.new, s3.changed) == (0, 0)  # unchanged ⇒ no new history rows
    site.gone.clear()
    products.extract_store(db, store, site.fetcher(), max_pages=10, delay=0)
    db.refresh(rows["b"])
    assert rows["b"].status == "active" and history(db, rows["b"].id)[-1][0] == "reappeared"
    runs = db.scalars(select(StoreObservation).where(StoreObservation.store_id == store.id)).all()
    assert len(runs) == 4 and all(r.observation_type == "product_extraction" for r in runs)


def test_page_budget_is_respected(db):
    host = f"q{uuid.uuid4().hex[:8]}.example"
    store = make_store(db, host)
    site = Site(host)
    site.prices = {f"x{i}": "1.00" for i in range(8)}
    stats = products.extract_store(db, store, site.fetcher(), max_pages=3, delay=0)
    assert stats.fetched == 3 and sum(p.startswith("/products/") for p in site.fetched) == 3


# ---------- task + API ----------


def test_extraction_api_and_task(db, client, monkeypatch):
    host = f"r{uuid.uuid4().hex[:8]}.example"
    store = make_store(db, host)
    site = Site(host)
    queued = []
    monkeypatch.setattr(v1, "_enqueue_products", queued.append)
    monkeypatch.setattr(tasks, "SafeFetcher", site.fetcher)
    monkeypatch.setattr(tasks.settings, "product_page_delay", 0)

    r = client.post(f"/api/v1/stores/{store.id}/products/extract")
    assert r.status_code == 202 and r.json()["job_type"] == "products"
    assert client.post(f"/api/v1/stores/{store.id}/products/extract").json()["id"] == r.json()["id"]  # in-flight reused
    assert len(queued) == 1

    assert tasks.extract_products.apply(args=[r.json()["id"]]).result == "succeeded"
    assert tasks.extract_products.apply(args=[r.json()["id"]]).result == "skipped"
    job = db.get(CrawlJob, uuid.UUID(r.json()["id"]))
    db.refresh(job)
    assert job.status == "succeeded" and job.result["evidence"]["new"] == 2

    page = client.get(f"/api/v1/stores/{store.id}/products").json()
    assert page["total"] == 2 and {p["current_price"] for p in page["items"]} == {"10.00", "20.00"}
    detail = client.get(f"/api/v1/products/{page['items'][0]['id']}").json()
    assert detail["history"][0]["change_type"] == "new"
    assert client.get(f"/api/v1/stores/{store.id}/products", params={"status": "bogus"}).status_code == 422
    assert client.get(f"/api/v1/products/{uuid.uuid4()}").status_code == 404


def test_only_verified_stores_are_extracted(db, client, monkeypatch):
    monkeypatch.setattr(v1, "_enqueue_products", lambda j: None)
    store = make_store(db, f"n{uuid.uuid4().hex[:8]}.example", status="not_ecommerce")
    assert client.post(f"/api/v1/stores/{store.id}/products/extract").status_code == 409
    assert client.post(f"/api/v1/stores/{uuid.uuid4()}/products/extract").status_code == 404
