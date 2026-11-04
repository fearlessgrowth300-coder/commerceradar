"""M3: deterministic platform detection. Inputs are hand-written snippets that mirror each
platform's public markup / headers / cookie names (not copies of any real store)."""

import pytest

from app.services.platforms import PLATFORMS, RULES, cookie_names, detect


def page(head: str = "", body: str = "") -> str:
    return f"<html><head><title>Shop</title>{head}</head><body>{body}</body></html>"


CASES = {
    "shopify": (
        page('<link rel="stylesheet" href="//cdn.shopify.com/s/files/1/0001/t/2/assets/base.css">',
             '<script>window.Shopify = window.Shopify || {}; Shopify.theme = {"name":"Dawn"};</script>'),
        {}, [],
    ),
    "woocommerce": (
        page('<meta name="generator" content="WooCommerce 9.3.3">'
             '<link rel="stylesheet" href="/wp-content/plugins/woocommerce/assets/css/woocommerce.css">'),
        {}, [],
    ),
    "wix": (
        page('<meta name="generator" content="Wix.com Website Builder">',
             '<img src="https://static.wixstatic.com/media/abc.jpg">'),
        {"X-Wix-Request-Id": "1700000000.123"}, [],
    ),
    "bigcommerce": (
        page('<link href="https://cdn11.bigcommerce.com/s-abc123/stencil/theme.css" rel="stylesheet">',
             '<script>var BCData = {"csrf_token":"x"};</script>'),
        {}, ["SHOP_SESSION_TOKEN"],
    ),
    "magento": (
        page('<script type="text/x-magento-init">{"*":{}}</script>',
             '<link href="/static/version1700000000/frontend/Magento/luma/en_US/css/styles-m.css">'),
        {}, ["form_key", "mage-cache-sessid"],
    ),
    "prestashop": (
        page('<meta name="generator" content="PrestaShop">',
             '<script>var prestashop = {"cart":{}};</script>'),
        {}, ["PrestaShop-a1b2c3d4e5f6"],
    ),
    "squarespace": (
        page('<!-- This is Squarespace. -->',
             '<script src="https://static1.squarespace.com/static/vta/abc/scripts/site-bundle.js"></script>'),
        {"Server": "Squarespace"}, ["crumb"],
    ),
    "opencart": (
        page('<link href="catalog/view/theme/default/stylesheet/stylesheet.css" rel="stylesheet">',
             '<a href="https://shop.test/index.php?route=product/category&path=20">Desktops</a>'),
        {}, ["OCSESSID"],
    ),
}  # fmt: skip


def test_every_named_platform_has_rules_and_a_case():
    assert set(RULES) == set(CASES) == set(PLATFORMS) - {"custom", "unknown"}


@pytest.mark.parametrize("expected", sorted(CASES))
def test_detects_each_platform_with_evidence(expected):
    html, headers, cookies = CASES[expected]
    r = detect(html, headers, cookies, is_confirmed_store=True)
    assert r.platform == expected
    assert 0.5 <= r.confidence <= 1.0
    hits = r.evidence["candidates"][expected]["hits"]
    assert hits and all(h["match"] and h["rule"] and h["weight"] > 0 for h in hits)
    assert r.method.startswith("deterministic")


def test_header_only_shopify():
    r = detect(page(), {"x-shopid": "42"}, [], is_confirmed_store=True)
    assert (r.platform, r.confidence) == ("shopify", 0.5)


def test_plain_wordpress_is_not_woocommerce():
    html = page('<link rel="stylesheet" href="/wp-content/themes/twentytwenty/style.css">')
    r = detect(html, {}, [], is_confirmed_store=True)
    assert r.platform == "unknown" and "only 1 points" in r.evidence["reason"]


def test_single_weak_mention_is_unknown():
    # e.g. a blog post linking to someone's myshopify.com store
    r = detect(page(body='<a href="https://friend.myshopify.com">friend</a>'), {}, [], is_confirmed_store=True)
    assert r.platform == "unknown" and r.confidence == 0.0


def test_conflicting_strong_evidence_is_unknown():
    html = page('<meta name="generator" content="WooCommerce 9.0">', '<script>window.Shopify = {};</script>')
    r = detect(html, {}, [], is_confirmed_store=True)
    assert r.platform == "unknown" and "ambiguous" in r.evidence["reason"]


def test_clear_winner_beats_minor_embed():
    # WooCommerce store that embeds a Shopify buy button: Woo is overwhelming, Shopify is minor
    html = page(
        '<meta name="generator" content="WooCommerce 9.0">'
        '<script src="/wp-content/plugins/woocommerce/assets/js/frontend/cart-fragments.js"></script>'
        "<script>var wc_add_to_cart_params = {};</script>",
        '<script src="https://sdks.shopifycdn.com/buy-button/latest/buy-button-storefront.min.js"></script>'
        '<a href="https://partner.myshopify.com/products/x">x</a>',
    )
    r = detect(html, {}, [], is_confirmed_store=True)
    assert r.platform == "woocommerce"


def test_no_fingerprint_custom_only_when_confirmed():
    assert detect(page(), {}, [], is_confirmed_store=True).platform == "custom"
    probable = detect(page(), {}, [], is_confirmed_store=False)
    assert (probable.platform, probable.confidence) == ("unknown", 0.0)


def test_cookie_names_never_keep_values():
    assert cookie_names(["_shopify_y=SECRET; Path=/; HttpOnly", "junk"]) == ["_shopify_y"]
