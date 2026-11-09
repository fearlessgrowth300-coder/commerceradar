"""Product extraction (spec §12) for verified stores.

Find product URLs (robots.txt sitemaps + homepage links), fetch a bounded number of
product pages politely, read schema.org Product JSON-LD (OpenGraph product tags as a
fallback), and persist what was actually observed — never inferred values.
History: a `product_observations` row is written when a product first appears and
whenever price / currency / availability / title change, disappears (404/410) or returns.
Images are referenced by URL only, never downloaded.
"""

import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import CrawlJob, Domain, Product, ProductObservation, Store, StoreObservation
from app.security.ssrf import BlockedDestination, DnsFailure
from app.services.fetcher import FetchError, SafeFetcher
from app.services import signals
from app.services.verification import PRODUCT_PATH, _robots

AVAILABILITY = {
    "instock": "in_stock", "outofstock": "out_of_stock", "soldout": "out_of_stock", "preorder": "pre_order",
    "presale": "pre_order", "backorder": "back_order", "discontinued": "discontinued",
    "limitedavailability": "limited", "onlineonly": "in_stock", "instoreonly": "in_store_only",
}  # fmt: skip
CURRENCY = re.compile(r"^[A-Z]{3}$")
TRACKED = ("title", "current_price", "currency", "availability")
MAX_SITEMAPS = 5


# ---------- pure extraction ----------


def _types(node: dict) -> set[str]:
    t = node.get("@type")
    return {str(x).rsplit("/", 1)[-1] for x in (t if isinstance(t, list) else [t]) if x}


def _walk(data: Any):
    if isinstance(data, list):
        for x in data:
            yield from _walk(x)
    elif isinstance(data, dict):
        yield data
        for key in ("@graph", "mainEntity", "itemListElement", "item"):
            if key in data:
                yield from _walk(data[key])


def _text(v: Any, limit: int) -> str | None:
    if isinstance(v, dict):
        v = v.get("name") or v.get("url") or v.get("@id")
    if isinstance(v, list):
        v = next((x for x in v if x), None)
        return _text(v, limit)
    v = " ".join(str(v).split()) if v not in (None, "") else ""
    return v[:limit] or None


def _price(v: Any) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    s = str(v).strip().replace(" ", "")
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", s):
        s = s.replace(",", "")  # 1,299.00
    elif re.fullmatch(r"\d+,\d{1,2}", s):
        s = s.replace(",", ".")  # 12,50
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return d.quantize(Decimal("0.01")) if d.is_finite() and 0 <= d < Decimal("1e12") else None


def _availability(v: Any) -> str | None:
    key = str(v or "").rsplit("/", 1)[-1].replace("_", "").replace(" ", "").lower()
    return AVAILABILITY.get(key)


def _offer(offers: Any) -> dict:
    """First usable offer. AggregateOffer → lowPrice (it's a range; we don't pretend it's one price)."""
    for o in offers if isinstance(offers, list) else [offers]:
        if not isinstance(o, dict):
            continue
        spec = o.get("priceSpecification")
        spec = spec[0] if isinstance(spec, list) and spec else spec if isinstance(spec, dict) else {}
        price = _price(o.get("price", spec.get("price")))
        aggregate = "AggregateOffer" in _types(o)
        if price is None and aggregate:
            price = _price(o.get("lowPrice"))
        cur = str(o.get("priceCurrency") or spec.get("priceCurrency") or "").upper()
        if price is not None or o.get("availability"):
            return {
                "price": price,
                "currency": cur if CURRENCY.match(cur) else None,
                "availability": _availability(o.get("availability")),
                "price_is_lowest_of_range": aggregate or None,
            }
    return {}


def extract_product(html: str, page_url: str) -> dict | None:
    """Observed product fields, or None when the page carries no product data."""
    tree = HTMLParser(html)
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text() or "null")
        except (json.JSONDecodeError, RecursionError):
            continue
        for item in _walk(data):
            types = _types(item)
            if not types & {"Product", "ProductGroup"}:
                continue
            offer = _offer(item.get("offers"))
            if not offer and isinstance(item.get("hasVariant"), list):
                offer = next((o for v in item["hasVariant"] if isinstance(v, dict) for o in [_offer(v.get("offers"))] if o), {})
            out = {
                "method": "json-ld",
                "title": _text(item.get("name"), 1000),
                "description": _text(item.get("description"), 5000),
                "image_url": _text(item.get("image"), 2000),
                "sku": _text(item.get("sku") or item.get("productID") or item.get("mpn"), 200),
                "category": _text(item.get("category"), 300),
                "current_price": offer.get("price"),
                "currency": offer.get("currency"),
                "availability": offer.get("availability"),
                "price_is_lowest_of_range": offer.get("price_is_lowest_of_range"),
            }
            if out["image_url"]:
                out["image_url"] = urljoin(page_url, out["image_url"])
            if out["title"] or out["current_price"] is not None:
                return out

    meta = {
        (n.attributes.get("property") or n.attributes.get("name") or "").lower(): (n.attributes.get("content") or "").strip()
        for n in tree.css("meta")
    }
    if meta.get("og:type", "").startswith("product") or "product:price:amount" in meta:
        cur = meta.get("product:price:currency", "").upper()
        out = {
            "method": "opengraph",
            "title": _text(meta.get("og:title"), 1000),
            "description": _text(meta.get("og:description"), 5000),
            "image_url": urljoin(page_url, meta["og:image"]) if meta.get("og:image") else None,
            "sku": None,
            "category": None,
            "current_price": _price(meta.get("product:price:amount")),
            "currency": cur if CURRENCY.match(cur) else None,
            "availability": _availability(meta.get("product:availability") or meta.get("og:availability")),
            "price_is_lowest_of_range": None,
        }
        if out["title"] or out["current_price"] is not None:
            return out
    return None


def sitemap_urls(xml_text: str) -> tuple[list[str], list[str]]:
    """→ (child sitemaps, page URLs). Tolerates namespaces; returns nothing on bad XML."""
    try:
        root = ET.fromstring(xml_text.encode("utf-8", "replace"))
    except ET.ParseError:
        return [], []
    locs = [(el.text or "").strip() for el in root.iter() if el.tag.rsplit("}", 1)[-1] == "loc"]
    if root.tag.rsplit("}", 1)[-1] == "sitemapindex":
        return [u for u in locs if u], []
    return [], [u for u in locs if u]


def is_product_url(url: str, host: str) -> bool:
    p = urlsplit(url)
    return len(url) <= 2000 and (p.hostname or "").removeprefix("www.") == host and bool(PRODUCT_PATH.search(p.path))


# ---------- jobs, queries ----------

EXTRACTABLE = ("confirmed_ecommerce", "probable_ecommerce")
IN_FLIGHT = ("queued", "running", "retrying")


class NotExtractable(Exception):
    pass


def queue_extraction(db: Session, store: Store, enqueue) -> tuple[CrawlJob, bool]:
    """→ (job, created). Reuses an in-flight extraction job for the same store."""
    if store.ecommerce_status not in EXTRACTABLE:
        raise NotExtractable(f"store is {store.ecommerce_status}; only verified stores are extracted")
    job = db.scalar(
        select(CrawlJob).where(
            CrawlJob.domain_id == store.domain_id, CrawlJob.job_type == "products", CrawlJob.status.in_(IN_FLIGHT)
        )
    )
    if job:
        return job, False
    host = db.scalar(select(Domain.normalized_domain).where(Domain.id == store.domain_id))
    job = CrawlJob(domain_id=store.domain_id, target_domain=host, job_type="products", status="queued")
    db.add(job)
    db.commit()
    try:
        enqueue(job.id)
    except Exception:
        job.status, job.error_message = "failed", "queue unavailable"
        db.commit()
        raise
    return job, True


def list_products(db: Session, store_id, status: str | None, page: int, page_size: int):
    q = select(Product).where(Product.store_id == store_id)
    if status:
        q = q.where(Product.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    items = db.scalars(q.order_by(Product.last_seen_at.desc(), Product.id).offset((page - 1) * page_size).limit(page_size)).all()
    return items, total


def product_history(db: Session, product_id, limit: int = 200) -> list[ProductObservation]:
    q = select(ProductObservation).where(ProductObservation.product_id == product_id)
    return list(db.scalars(q.order_by(ProductObservation.observed_at.desc()).limit(limit)))


# ---------- network + persistence ----------


@dataclass
class ExtractStats:
    candidates: int = 0
    fetched: int = 0
    extracted: int = 0
    no_product_data: int = 0
    new: int = 0
    changed: int = 0
    removed: int = 0
    errors: int = 0
    disallowed: int = 0
    notes: list[str] = field(default_factory=list)


def _sitemaps(fetcher: SafeFetcher, base: str, robots, host: str, stats: ExtractStats) -> list[str]:
    """Product URLs from sitemaps (robots.txt `Sitemap:` lines, else /sitemap.xml). Prefers
    product-specific child sitemaps (Shopify sitemap_products_*, Woo product-sitemap.xml)."""
    queue = list(robots.site_maps() or []) or [urljoin(base, "/sitemap.xml")]
    found: list[str] = []
    seen = 0
    while queue and seen < MAX_SITEMAPS:
        sm = queue.pop(0)
        if not robots.can_fetch(settings.bot_user_agent, sm):
            continue
        seen += 1
        try:
            r = fetcher.fetch(sm)
        except (FetchError, BlockedDestination, DnsFailure):
            continue
        if r.status >= 400:
            continue
        children, urls = sitemap_urls(r.text)
        queue = sorted(children, key=lambda u: "product" not in u.lower()) + queue
        found += [u for u in urls if is_product_url(u, host)]
    stats.notes.append(f"{seen} sitemaps read, {len(found)} product URLs")
    return found


def _record(db: Session, product: Product, change: str, now: datetime, detail: dict | None = None) -> None:
    db.add(
        ProductObservation(
            product_id=product.id,
            observed_at=now,
            change_type=change,
            price=product.current_price,
            currency=product.currency,
            availability=product.availability,
            title=product.title,
            details=detail or {},
        )
    )


def save_product(db: Session, store_id, url: str, data: dict, now: datetime) -> str:
    """→ 'new' | 'changed' | 'unchanged'. Current fields = what this page shows now."""
    product = db.scalar(select(Product).where(Product.store_id == store_id, Product.product_url == url))
    fields = {k: data[k] for k in ("title", "description", "image_url", "sku", "category", "current_price", "currency", "availability")}
    if product is None:
        product = Product(store_id=store_id, product_url=url, first_seen_at=now, last_seen_at=now, status="active",
                          external_product_id=data["sku"], extraction_method=data["method"], **fields)  # fmt: skip
        db.add(product)
        db.flush()
        _record(db, product, "new", now)
        return "new"
    diff = {k: [_json(getattr(product, k)), _json(v)] for k, v in fields.items() if k in TRACKED and getattr(product, k) != v}
    reappeared = product.status == "removed"
    for k, v in fields.items():
        setattr(product, k, v)
    product.external_product_id = data["sku"] or product.external_product_id
    product.extraction_method = data["method"]
    product.last_seen_at = now
    product.status = "active"
    if reappeared or diff:
        _record(db, product, "reappeared" if reappeared else "changed", now, diff)
        return "changed"
    return "unchanged"


def _json(v: Any) -> Any:
    return str(v) if isinstance(v, Decimal) else v


def parse_shop_meta(text: str) -> dict | None:
    """Business location from a Shopify /meta.json body: {country, region, city} or None.
    Only a valid 2-letter country code counts; region/city are kept as the merchant typed them."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    country = str(data.get("country") or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        return None
    clean = lambda v: (str(v).strip()[:100] or None) if v else None  # noqa: E731
    return {"country": country, "region": clean(data.get("province")), "city": clean(data.get("city"))}


def update_location(store: Store, fetcher: SafeFetcher, base: str, robots, ua: str) -> bool:
    """Shopify stores publish their settings' business address at /meta.json. One request, robots-checked."""
    url = urljoin(base, "/meta.json")
    if not robots.can_fetch(ua, url):
        return False
    try:
        r = fetcher.fetch(url)
    except (FetchError, BlockedDestination, DnsFailure):
        return False
    loc = parse_shop_meta(r.text) if r.status == 200 else None
    store.location_checked_at = datetime.now(UTC)
    if loc:
        store.business_country, store.business_region, store.business_city = loc["country"], loc["region"], loc["city"]
        store.location_source = "shopify_meta"
    return loc is not None


def extract_store(db: Session, store: Store, fetcher: SafeFetcher, max_pages: int, delay: float) -> ExtractStats:
    """One bounded pass over a store. Network errors on single pages are counted, not raised;
    an unreachable store raises FetchError so the task can retry later."""
    stats = ExtractStats()
    had_products = db.scalar(select(func.count()).select_from(Product).where(Product.store_id == store.id)) > 0
    host = db.scalar(select(Domain.normalized_domain).where(Domain.id == store.domain_id))
    base = store.canonical_url or f"https://{host}/"
    host = (urlsplit(base).hostname or host).removeprefix("www.")
    robots = _robots(fetcher, base)  # FetchError propagates → retry
    ua = settings.bot_user_agent
    delay = max(delay, min(float(robots.crawl_delay(ua) or 0), 10.0))
    if store.primary_platform == "shopify":
        update_location(store, fetcher, base, robots, ua)

    urls = _sitemaps(fetcher, base, robots, host, stats)
    if len(urls) < max_pages and robots.can_fetch(ua, base):
        try:
            home = fetcher.fetch(base)
            if home.status < 400:
                for a in HTMLParser(home.text).css("a[href]"):
                    u = urljoin(home.url, a.attributes.get("href") or "").split("#")[0]
                    if is_product_url(u, host):
                        urls.append(u)
        except (FetchError, BlockedDestination, DnsFailure):
            pass

    # Half the budget re-checks the least recently seen known products (price changes, removals),
    # the rest goes to URLs we have never seen.
    known = list(
        db.scalars(select(Product.product_url).where(Product.store_id == store.id).order_by(Product.last_seen_at).limit(max_pages))
    )
    known_set = set(known)
    fresh = list(dict.fromkeys(u for u in urls if u not in known_set))
    revisit = known[: max_pages // 2] if fresh else known
    plan = list(dict.fromkeys(revisit + fresh + known))[:max_pages]  # unused budget → more re-checks
    stats.candidates = len(set(urls) | known_set)

    now = datetime.now(UTC)
    for i, url in enumerate(dict.fromkeys(plan)):
        if not robots.can_fetch(ua, url):
            stats.disallowed += 1
            continue
        if i:
            time.sleep(delay)
        try:
            r = fetcher.fetch(url)
        except (FetchError, BlockedDestination, DnsFailure):
            stats.errors += 1
            continue
        stats.fetched += 1
        if r.status in (404, 410):
            product = db.scalar(select(Product).where(Product.store_id == store.id, Product.product_url == url))
            if product and product.status != "removed":
                product.status = "removed"
                _record(db, product, "removed", now, {"http_status": r.status})
                stats.removed += 1
            continue
        if r.status >= 400 or "html" not in r.content_type:
            stats.errors += 1
            continue
        data = extract_product(r.text, r.url)
        if data is None:
            stats.no_product_data += 1
            continue
        stats.extracted += 1
        outcome = save_product(db, store.id, url, data, now)
        stats.new += outcome == "new"
        stats.changed += outcome == "changed"
    db.add(
        StoreObservation(
            store_id=store.id,
            observation_type="product_extraction",
            observation_data={k: v for k, v in stats.__dict__.items()},
            observed_at=now,
        )
    )
    store.last_observed_at = now
    if had_products and (stats.new or stats.changed or stats.removed):
        store.last_changed_at = now  # catalogue moving ⇒ monitoring re-checks sooner
    db.flush()
    signals.refresh_store(db, store, now)
    db.commit()
    return stats
