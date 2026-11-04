"""Ecommerce verification: fetch (homepage + at most one product page), extract evidence,
classify with deterministic scoring. Words like "shop"/"cart" in prose score nothing;
only functional or structured signals do."""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from selectolax.parser import HTMLParser
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import CrawlJob, Domain, PlatformDetection, Store, StoreObservation
from app.security.ssrf import BlockedDestination, DnsFailure
from app.services import identity, monitoring, platforms, signals
from app.services.signals import copyright_year
from app.services.fetcher import FetchError, SafeFetcher

# Signal weights. Thresholds: confirmed >= 7 (and needs a strong signal), probable >= 4, uncertain >= 2.
WEIGHTS = {
    "product_structured_data": 3,
    "add_to_cart": 3,
    "og_product": 2,
    "cart_link": 2,
    "product_links": 2,
    "prices": 1,
}
STRONG = ("product_structured_data", "add_to_cart")

CART_PATH = re.compile(r"/(cart|basket|bag|checkout)(/|$)", re.I)
PRODUCT_PATH = re.compile(r"/(products?|item|p|shop/[^/]+|collections/[^/]+/products)/[^/]+", re.I)
PRODUCT_QUERY = re.compile(r"[?&](product_id|product|add-to-cart|route=product/product)=", re.I)
ADD_TO_CART_ATTR = re.compile(r"add[-_ ]?to[-_ ]?(cart|bag|basket)|addtocart|cart/add", re.I)
ADD_TO_CART_TEXT = re.compile(r"\badd to (cart|bag|basket)\b", re.I)
PRICE = re.compile(
    r"(?:[$€£¥₦₹]|\b(?:USD|EUR|GBP|NGN|CAD|AUD|INR)\b)\s?\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{2})?", re.I
)


SHOPIFY_SHOP = re.compile(r"Shopify\.shop\s*=\s*[\"']([a-z0-9][a-z0-9-]*\.myshopify\.com)[\"']")


def _canonical(tree, meta: dict, page_url: str) -> str | None:
    node = tree.css_first('link[rel="canonical"]')
    href = (node.attributes.get("href") if node else None) or meta.get("og:url")
    return urljoin(page_url, href)[:500] if href else None


@dataclass
class VerificationResult:
    status: str
    confidence: float
    resolution: str  # resolved | nxdomain | blocked | error
    evidence: dict[str, Any] = field(default_factory=dict)
    final_url: str | None = None
    site_name: str | None = None
    platform: platforms.PlatformResult | None = None  # only for confirmed/probable stores
    serving: dict[str, Any] = field(default_factory=dict)  # ip, identity headers, cookie names (identity evidence)
    redirects: list[str] = field(default_factory=list)


# ---------- pure parsing / scoring (unit-tested with fixtures) ----------


def _jsonld_types(data: Any, out: set[str], offers: list[bool]) -> None:
    if isinstance(data, list):
        for item in data:
            _jsonld_types(item, out, offers)
    elif isinstance(data, dict):
        t = data.get("@type")
        for name in t if isinstance(t, list) else [t]:
            if isinstance(name, str):
                out.add(name)
        if "offers" in data and any(n in ("Product", "ProductGroup") for n in (t if isinstance(t, list) else [t])):
            offers.append(True)
        for key in ("@graph", "itemListElement", "item", "hasVariant", "offers"):
            if key in data:
                _jsonld_types(data[key], out, offers)


def extract_signals(html: str, page_url: str) -> dict[str, Any]:
    tree = HTMLParser(html)
    host = (urlsplit(page_url).hostname or "").removeprefix("www.")

    title_node = tree.css_first("title")
    meta = {
        (n.attributes.get("property") or n.attributes.get("name") or "").lower(): n.attributes.get("content") or ""
        for n in tree.css("meta")
    }

    types: set[str] = set()
    offers: list[bool] = []
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            _jsonld_types(json.loads(node.text() or "null"), types, offers)
        except (json.JSONDecodeError, RecursionError):
            continue

    cart_links: set[str] = set()
    product_links: list[str] = []
    for a in tree.css("a[href]"):
        url = urljoin(page_url, a.attributes.get("href") or "")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or (parts.hostname or "").removeprefix("www.") != host:
            continue
        if CART_PATH.search(parts.path):
            cart_links.add(parts.path)
        elif (PRODUCT_PATH.search(parts.path) or PRODUCT_QUERY.search("?" + parts.query)) and url not in product_links:
            product_links.append(url.split("#")[0])

    add_to_cart = []
    for n in tree.css("form, button, input[type=submit], input[type=button], a"):
        attrs = " ".join(str(v) for k, v in n.attributes.items() if k in ("action", "name", "class", "id", "href", "data-action"))
        text = n.attributes.get("value") or (n.text(strip=True) if n.tag != "form" else "")
        if ADD_TO_CART_ATTR.search(attrs) or (n.tag != "a" and ADD_TO_CART_TEXT.search(text or "")):
            add_to_cart.append(n.tag)

    body = tree.body.text(separator=" ") if tree.body else ""
    prices = PRICE.findall(body)

    return {
        "title": (title_node.text(strip=True)[:300] if title_node else None),
        "site_name": meta.get("og:site_name") or None,
        "jsonld_types": sorted(types)[:30],
        "product_structured_data": bool({"Product", "ProductGroup"} & types) and (bool(offers) or "Offer" in types),
        "og_product": meta.get("og:type", "").startswith("product") or "product:price:amount" in meta,
        "cart_link": sorted(cart_links)[:5],
        "add_to_cart": len(add_to_cart),
        "product_links": product_links[:10],
        "product_link_count": len(product_links),
        "price_count": len(prices),
        "text_chars": len(" ".join(body.split())),
        "copyright_year": copyright_year(body),
        "shopify_shop": (m.group(1) if (m := SHOPIFY_SHOP.search(html)) else None),
        "canonical": _canonical(tree, meta, page_url),
    }


def score(signals: dict[str, Any]) -> tuple[int, list[str]]:
    hits = []
    if signals.get("product_structured_data"):
        hits.append("product_structured_data")
    if signals.get("add_to_cart"):
        hits.append("add_to_cart")
    if signals.get("og_product"):
        hits.append("og_product")
    if signals.get("cart_link"):
        hits.append("cart_link")
    if signals.get("product_link_count", 0) >= 3:
        hits.append("product_links")
    if signals.get("price_count", 0) >= 3:
        hits.append("prices")
    return sum(WEIGHTS[h] for h in hits), hits


MIN_TEXT_CHARS = 200


def classify(points: int, hits: list[str]) -> tuple[str, float]:
    confidence = round(min(points / 10, 1.0), 2)
    if points >= 7 and any(h in STRONG for h in hits):
        return "confirmed_ecommerce", confidence
    if points >= 4:
        return "probable_ecommerce", confidence
    if points >= 2:
        return "uncertain", confidence
    return "not_ecommerce", round(1 - confidence, 2)


def merge_signals(home: dict[str, Any], product: dict[str, Any]) -> dict[str, Any]:
    merged = dict(home)
    for key in ("product_structured_data", "og_product"):
        merged[key] = home[key] or product[key]
    merged["add_to_cart"] = home["add_to_cart"] + product["add_to_cart"]
    merged["cart_link"] = sorted(set(home["cart_link"]) | set(product["cart_link"]))[:5]
    merged["price_count"] = max(home["price_count"], product["price_count"])
    return merged


# ---------- network ----------


def _robots(fetcher: SafeFetcher, base: str) -> RobotFileParser:
    rp = RobotFileParser()
    res = fetcher.fetch(urljoin(base, "/robots.txt"))  # FetchError (incl. 5xx) propagates → retry later
    if res.status >= 400:
        rp.allow_all = True  # RFC 9309: 4xx means no restrictions
    else:
        rp.parse(res.text.splitlines())
    return rp


def verify(host: str, fetcher: SafeFetcher) -> VerificationResult:
    """Raises FetchError for transient failures (caller retries)."""
    ua = settings.bot_user_agent
    try:
        base = f"https://{host}/"
        try:
            robots = _robots(fetcher, base)
        except FetchError:
            base = f"http://{host}/"  # some small stores still have no TLS
            robots = _robots(fetcher, base)
        if not robots.can_fetch(ua, base):
            return VerificationResult("uncertain", 0.0, "resolved", {"reason": "robots.txt disallows homepage"})

        home = fetcher.fetch(base)
    except DnsFailure as e:
        return VerificationResult("temporarily_unreachable", 0.0, "nxdomain", {"reason": str(e)})
    except BlockedDestination as e:
        return VerificationResult("blocked", 1.0, "blocked", {"reason": str(e)})

    serving = {
        "ip": home.ip,
        "headers": {k: home.headers[k] for k in ("powered-by", "server", "x-shopid", "x-shopify-stage") if k in home.headers},
        "cookies": platforms.cookie_names(home.set_cookies),
    }
    evidence: dict[str, Any] = {"homepage": {"url": home.url, "status": home.status, "redirects": home.redirects, "ip": home.ip}}
    if home.status >= 400:
        evidence["reason"] = f"homepage returned HTTP {home.status}"  # e.g. 403 bot wall: we don't bypass it
        return VerificationResult("uncertain", 0.0, "resolved", evidence, final_url=home.url)
    if "html" not in home.content_type:
        evidence["reason"] = f"homepage is not HTML ({home.content_type or 'no content-type'})"
        return VerificationResult("not_ecommerce", 0.5, "resolved", evidence, final_url=home.url)

    signals = extract_signals(home.text, home.url)
    evidence["homepage"]["signals"] = signals

    # One product page usually carries the Product JSON-LD the homepage lacks.
    pages_html = home.text
    product_url = next((u for u in signals["product_links"] if robots.can_fetch(ua, u)), None)
    if product_url and not signals["product_structured_data"]:
        try:
            page = fetcher.fetch(product_url)
            if page.status < 400 and "html" in page.content_type:
                product_signals = extract_signals(page.text, page.url)
                evidence["product_page"] = {"url": page.url, "status": page.status, "signals": product_signals}
                signals = merge_signals(signals, product_signals)
                pages_html += "\n" + page.text
        except (FetchError, BlockedDestination, DnsFailure) as e:
            evidence["product_page"] = {"url": product_url, "error": str(e)}

    points, hits = score(signals)
    status, confidence = classify(points, hits)
    if status == "not_ecommerce" and evidence["homepage"]["signals"]["text_chars"] < MIN_TEXT_CHARS:
        # Empty shell (JS-rendered app, geo/consent interstitial): absence of signals proves nothing.
        status, confidence = "uncertain", 0.0
        evidence["reason"] = "homepage has almost no server-rendered content; cannot judge without executing JS"
    evidence["score"] = {"points": points, "signals_hit": hits, "weights": WEIGHTS}
    platform = None
    if status in ("confirmed_ecommerce", "probable_ecommerce"):
        platform = platforms.detect(
            pages_html, home.headers, platforms.cookie_names(home.set_cookies), status == "confirmed_ecommerce"
        )
    return VerificationResult(
        status,
        confidence,
        "resolved",
        evidence,
        final_url=home.url,
        site_name=signals["site_name"] or signals["title"],
        platform=platform,
        serving=serving,
        redirects=home.redirects,
    )


# ---------- persistence ----------


def apply_result(db: Session, job: CrawlJob, result: VerificationResult) -> None:
    now = datetime.now(UTC)
    domain = db.get(Domain, job.domain_id)
    domain.verification_status = result.status
    domain.verification_confidence = result.confidence
    domain.current_resolution_status = result.resolution
    domain.last_checked_at = now
    domain.last_seen_at = now

    is_store = result.status in ("confirmed_ecommerce", "probable_ecommerce")
    if is_store:
        # Store identity (M10): one store per storefront, however many domains point at it.
        home = (result.evidence.get("homepage") or {}).get("signals") or {}
        store = identity.resolve(db, domain, result.status, home, result.serving, result.final_url,
                                 result.redirects, home.get("canonical"), now)  # fmt: skip
    else:
        store = identity.primary_store_of(db, domain.id)  # negative checks only move the store via its primary
    if store is not None:
        if is_store:
            # ecommerce_status keeps the last *positive* classification; a negative check only
            # moves activity (after repeated evidence) — see monitoring.update_activity.
            store.ecommerce_status = result.status
            store.store_name = (result.site_name or store.store_name or "")[:300] or None
            store.canonical_url = result.final_url
            store.last_verified_at = now
            store.last_observed_at = now
        db.flush()
        monitoring.update_activity(db, store, result.status, result.confidence, result.resolution, now)
        if is_store and result.platform:
            pf = result.platform
            old = store.primary_platform
            if old not in (None, "unknown", "custom") and pf.platform not in ("unknown", old):
                store.last_changed_at = now
                db.add(
                    StoreObservation(
                        store_id=store.id,
                        observation_type="platform_change",
                        observation_data={"from": old, "to": pf.platform, "confidence": pf.confidence},
                        observed_at=now,
                    )
                )
            store.primary_platform = pf.platform
            store.platform_confidence = pf.confidence
            db.add(
                PlatformDetection(
                    store_id=store.id,
                    platform=pf.platform,
                    confidence=pf.confidence,
                    evidence=pf.evidence,
                    detection_method=pf.method,
                    detected_at=now,
                )
            )
        db.add(
            StoreObservation(
                store_id=store.id,
                observation_type="verification",
                observation_data={"status": result.status, "confidence": result.confidence, "job_id": str(job.id)},
                observed_at=now,
            )
        )

    job.status = "succeeded"
    job.completed_at = now
    job.error_message = None
    job.result = {
        "status": result.status,
        "confidence": result.confidence,
        "resolution": result.resolution,
        "evidence": result.evidence,
        "platform": (
            {"platform": result.platform.platform, "confidence": result.platform.confidence, "evidence": result.platform.evidence}
            if result.platform
            else None
        ),
    }
    if store is not None:
        db.flush()
        signals.refresh_store(db, store, now)
    db.commit()
