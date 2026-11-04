"""Deterministic ecommerce platform detection (spec §10).

Each platform is an independent adapter: a list of fingerprint rules. A rule looks at
the HTML (`html`), a response header (`header:<name>`) or a cookie name (`cookie`).
Weights: 4 = specific to that platform on its own, 3 = strong, 2 = supporting, 1 = weak.
No AI, no paid calls. Insufficient or ambiguous evidence ⇒ "unknown".
"""

import re
from dataclasses import dataclass, field
from typing import Any

METHOD = "deterministic_fingerprint_v1"
MIN_POINTS = 4  # one platform-specific fingerprint, or a strong + a supporting one
FULL_POINTS = 8  # confidence 1.0
PLATFORMS = (
    "shopify", "woocommerce", "wix", "bigcommerce", "magento",
    "prestashop", "squarespace", "opencart", "custom", "unknown",
)  # fmt: skip

# (source, pattern, weight, label). Patterns are case-insensitive regexes.
RULES: dict[str, list[tuple[str, str, int, str]]] = {
    "shopify": [
        ("header:x-shopid", r".", 4, "x-shopid header"),
        ("header:x-shopify-stage", r".", 4, "x-shopify-stage header"),
        ("cookie", r"^_shopify_(y|s|sa_t|sa_p)$|^cart_currency$", 3, "Shopify cookies"),
        ("html", r"cdn\.shopify\.com", 3, "cdn.shopify.com assets"),
        ("html", r"window\.Shopify\s*=|Shopify\.theme\s*=", 4, "Shopify JS globals"),
        ("html", r"shopify-digital-wallet|shopify-checkout-api-token", 3, "Shopify checkout meta"),
        ("html", r"[a-z0-9-]+\.myshopify\.com", 2, "myshopify.com reference"),
    ],
    "woocommerce": [
        ("html", r'<meta[^>]+name="generator"[^>]+content="WooCommerce', 4, "WooCommerce generator meta"),
        ("html", r"/wp-content/plugins/woocommerce/", 4, "WooCommerce plugin assets"),
        ("html", r"wc_add_to_cart_params|woocommerce_params|wc-cart-fragments", 3, "WooCommerce JS"),
        ("html", r'class="[^"]*\bwoocommerce\b', 2, "woocommerce CSS class"),
        ("cookie", r"^woocommerce_|^wp_woocommerce_session_", 4, "WooCommerce cookies"),
        ("html", r"/wp-content/|/wp-includes/", 1, "WordPress paths"),
    ],
    "wix": [
        ("header:x-wix-request-id", r".", 4, "x-wix-request-id header"),
        ("html", r'<meta[^>]+name="generator"[^>]+content="Wix\.com', 4, "Wix generator meta"),
        ("html", r"static\.parastorage\.com|static\.wixstatic\.com", 3, "Wix static assets"),
        ("html", r"wix-ecommerce|wixstores|ecom-platform", 2, "Wix Stores"),
    ],
    "bigcommerce": [
        ("html", r"cdn\d*\.bigcommerce\.com", 4, "BigCommerce CDN"),
        ("html", r"\bBCData\s*=|stencil-utils|stencilBootstrap", 3, "BigCommerce Stencil JS"),
        ("cookie", r"^SHOP_SESSION_TOKEN$|^fornax_anonymousId$", 3, "BigCommerce cookies"),
        ("html", r"/cart\.php|/checkout\.php|/login\.php\?action=", 1, "BigCommerce-style PHP routes"),
    ],
    "magento": [
        ("header:x-magento-cache-debug", r".", 4, "x-magento-cache-debug header"),
        ("header:x-magento-tags", r".", 4, "x-magento-tags header"),
        ("html", r'type="text/x-magento-init"|data-mage-init=', 4, "Magento 2 init scripts"),
        ("html", r"/static/(version\d+/)?frontend/[^/\"']+/[^/\"']+/", 3, "Magento static frontend path"),
        ("html", r"Mage\.Cookies|/skin/frontend/|varien/js", 3, "Magento 1 assets"),
        ("cookie", r"^mage-cache-(storage|sessid)$|^form_key$", 3, "Magento cookies"),
    ],
    "prestashop": [
        ("html", r'<meta[^>]+name="generator"[^>]+content="PrestaShop', 4, "PrestaShop generator meta"),
        ("html", r"\bvar\s+prestashop\s*=|prestashop\.urls", 4, "PrestaShop JS global"),
        ("cookie", r"^PrestaShop-[0-9a-f]+$", 4, "PrestaShop cookie"),
        ("header:powered-by", r"prestashop", 4, "Powered-By header"),
        ("html", r"/modules/ps_[a-z_]+/", 3, "PrestaShop modules"),
    ],
    "squarespace": [
        ("html", r"static1\.squarespace\.com|assets\.squarespace\.com", 4, "Squarespace assets"),
        ("html", r"Static\.SQUARESPACE_CONTEXT|squarespace-commerce", 4, "Squarespace context"),
        ("html", r"<!-- This is Squarespace\. -->", 4, "Squarespace comment"),
        ("cookie", r"^crumb$|^ss_cvr$|^ss_cvt$", 2, "Squarespace cookies"),
        ("header:server", r"squarespace", 3, "Server header"),
    ],
    "opencart": [
        ("cookie", r"^OCSESSID$", 4, "OpenCart session cookie"),
        ("html", r"catalog/view/(theme|javascript)/", 4, "OpenCart catalog assets"),
        ("html", r"index\.php\?route=(product|common|checkout)/", 3, "OpenCart routes"),
    ],
}

_COMPILED = {p: [(s, re.compile(pat, re.I), w, lbl) for s, pat, w, lbl in rs] for p, rs in RULES.items()}


@dataclass
class PlatformResult:
    platform: str
    confidence: float
    method: str = METHOD
    evidence: dict[str, Any] = field(default_factory=dict)


def cookie_names(set_cookie_headers: list[str]) -> list[str]:
    return [h.split("=", 1)[0].strip() for h in set_cookie_headers if "=" in h]


def _match(source: str, rx: re.Pattern, html: str, headers: dict[str, str], cookies: list[str]) -> str | None:
    """Return a short snippet proving the hit, or None."""
    if source == "html":
        m = rx.search(html)
        return html[max(m.start() - 20, 0) : m.end() + 20].strip()[:120] if m else None
    if source == "cookie":
        return next((c for c in cookies if rx.search(c)), None)
    name = source.removeprefix("header:")
    value = headers.get(name)
    return f"{name}: {value[:80]}" if value is not None and rx.search(value) else None


def detect(html: str, headers: dict[str, str], cookies: list[str], is_confirmed_store: bool) -> PlatformResult:
    """Score every platform, pick the clear winner; otherwise unknown (or custom, see below)."""
    headers = {k.lower(): v for k, v in headers.items()}
    scores: dict[str, dict[str, Any]] = {}
    for platform, rules in _COMPILED.items():
        hits = []
        for source, rx, weight, label in rules:
            snippet = _match(source, rx, html, headers, cookies)
            if snippet is not None:
                hits.append({"rule": label, "source": source, "weight": weight, "match": snippet})
        if hits:
            scores[platform] = {"points": sum(h["weight"] for h in hits), "hits": hits}

    ranked = sorted(scores.items(), key=lambda kv: kv[1]["points"], reverse=True)
    evidence: dict[str, Any] = {"candidates": dict(ranked), "min_points": MIN_POINTS}
    if not ranked:
        if is_confirmed_store:
            # Clearly a working store, and none of the supported platforms leaves a trace.
            evidence["reason"] = "confirmed store with no fingerprint of any supported platform"
            return PlatformResult("custom", 0.3, evidence=evidence)
        evidence["reason"] = "no platform fingerprints found"
        return PlatformResult("unknown", 0.0, evidence=evidence)

    best, top = ranked[0]
    if top["points"] < MIN_POINTS:
        evidence["reason"] = f"best candidate {best} has only {top['points']} points"
        return PlatformResult("unknown", 0.0, evidence=evidence)
    if len(ranked) > 1 and ranked[1][1]["points"] >= MIN_POINTS and ranked[1][1]["points"] * 2 > top["points"]:
        evidence["reason"] = f"ambiguous: {best} vs {ranked[1][0]}"
        return PlatformResult("unknown", 0.0, evidence=evidence)
    return PlatformResult(best, round(min(top["points"] / FULL_POINTS, 1.0), 2), evidence=evidence)
