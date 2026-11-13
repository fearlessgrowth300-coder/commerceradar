import json
from types import SimpleNamespace

from app.services import catalog


def test_language_from_html_lang_or_og_locale():
    assert catalog.language('<html lang="en-US"><head>') == "en"
    assert catalog.language("<html class=x lang=es>") == "es"
    assert catalog.language('<html><head><meta property="og:locale" content="de_DE">') == "de"
    assert catalog.language("<html><body>") is None


def test_formats_digital_is_observed_and_apps_are_signatures():
    digital = [{"variants": [{"requires_shipping": False}]}, {"variants": [{"requires_shipping": False}]}, {"variants": [{"requires_shipping": True}]}]
    f = catalog.formats('<script src="https://cdn.printful.com/x.js"></script>', digital)
    assert f == {"pod": True, "cod": False, "dropshipping": False, "digital": True}
    f = catalog.formats("<html>Releasit COD form</html>", [{"variants": [{"requires_shipping": True}]}])
    assert f["cod"] and not f["digital"] and not f["pod"]
    assert catalog.formats("", [])["digital"] is False  # no products ≠ digital


def test_classify_group_then_subcategory_only_with_specific_evidence():
    pets = [{"title": "Rope chew toy for dogs", "product_type": "Dog Toy", "tags": ["dog"]}] * 3
    assert catalog.classify("Happy Paws", pets)[:2] == ("Pet Supplies", "Pet Toys")
    generic = [{"title": "Premium dog item", "product_type": "", "tags": "dog, puppy"}] * 3
    group, sub, conf = catalog.classify(None, generic)
    assert group == "Pet Supplies" and sub is None and 0 < conf <= 1  # generic words: group only
    spanish = [{"title": "Zapatillas running", "product_type": "Zapatillas", "tags": []}] * 2
    assert catalog.classify(None, spanish)[:2] == ("Clothing & Shoes & Accessories", "Shoes")
    assert catalog.classify("Shop", [{"title": "Thing"}]) == (None, None, 0.0)  # no evidence, no label


def test_summarize_product_dates():
    s = catalog.summarize([{"created_at": "2025-05-04T10:00:00-04:00"}, {"created_at": "2023-10-06T08:00:00Z"}, {}])
    assert s["published_products"] == 3
    assert s["first_product_at"].year == 2023 and s["latest_product_at"].year == 2025


class _Robots:
    def __init__(self, allow=True):
        self.allow = allow

    def can_fetch(self, ua, url):
        return self.allow


class _Fetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def fetch(self, url):
        self.calls.append(url)
        if "products.json" in url:
            page = int(url.rsplit("page=", 1)[1])
            body = json.dumps({"products": self.pages[page - 1] if page <= len(self.pages) else []})
            return SimpleNamespace(status=200, text=body, content_type="application/json")
        return SimpleNamespace(status=200, text='<html lang="fr"><script src="//dsers.com/a.js"></script>', content_type="text/html")


def _store(platform="shopify"):
    return SimpleNamespace(primary_platform=platform, store_name="Boutique Chaussures", language=None, published_products=None,
                           first_product_at=None, latest_product_at=None, is_pod=None, is_cod=None, is_dropshipping=None,
                           is_digital=None, category_group=None, category=None, category_confidence=None, catalog_checked_at=None)  # fmt: skip


def test_update_catalog_reads_products_and_homepage():
    full = [{"title": f"Chaussures {i}", "product_type": "Shoes", "created_at": f"2024-01-{i % 28 + 1:02d}T00:00:00Z",
             "variants": [{"requires_shipping": True}]} for i in range(250)]  # fmt: skip
    f = _Fetcher([full, full[:3]])
    st = _store()
    assert catalog.update_catalog(st, f, "https://x.example/", _Robots(), "bot") is True
    assert st.language == "fr" and st.published_products == 253 and st.is_dropshipping and not st.is_digital
    assert (st.category_group, st.category) == ("Clothing & Shoes & Accessories", "Shoes")
    assert sum("products.json" in c for c in f.calls) == 2  # stopped after the short page


def test_update_catalog_respects_robots_and_skips_products_for_other_platforms():
    f = _Fetcher([[{"title": "x"}]])
    assert catalog.update_catalog(_store(), f, "https://x.example/", _Robots(False), "bot") is False
    assert f.calls == []
    f = _Fetcher([[{"title": "x"}]])
    catalog.update_catalog(_store("woocommerce"), f, "https://x.example/", _Robots(), "bot")
    assert not any("products.json" in c for c in f.calls)


def test_store_filters_and_categories_endpoint(client):
    cats = client.get("/api/v1/categories").json()
    assert {"group": "Pet Supplies", "subcategories": ["Pet Food", "Pet Toys", "Pet Health", "Pet Clothing"]} in cats
    ok = {"language": "en", "category": "Pet Supplies", "format": "pod", "first_product_from": "2024-01-01", "sort": "latest_product"}
    assert client.get("/api/v1/stores", params=ok).status_code == 200
    assert client.get("/api/v1/stores", params={"format": "nft"}).status_code == 422
    assert client.get("/api/v1/exports/stores.csv", params={"format": "nft"}).status_code == 422
    header = client.get("/api/v1/exports/stores.csv", params={"limit": 1}).text.splitlines()[0]
    assert "language,category_group,category,formats,published_products,first_product_at,latest_product_at" in header


def test_address_country_only_from_stated_two_letter_codes():
    ld = lambda d: f'<script type="application/ld+json">{json.dumps(d)}</script>'  # noqa: E731
    org = {"@type": "Organization", "address": {"@type": "PostalAddress", "addressCountry": "it"}}
    assert catalog.address_country(ld(org)) == "IT"
    assert catalog.address_country(ld({"@graph": [{"address": {"addressCountry": {"name": "DE"}}}]})) == "DE"
    assert catalog.address_country(ld({"address": {"addressCountry": "United States"}})) is None  # names not mapped
    two = ld({"address": {"addressCountry": "US"}}) + ld({"address": {"addressCountry": "GB"}})
    assert catalog.address_country(two) is None  # ambiguous
    assert catalog.address_country("<script type=application/ld+json>{bad</script>") is None


def test_non_shopify_store_uses_observed_products_and_structured_country():
    class Home(_Fetcher):
        def fetch(self, url):
            self.calls.append(url)
            body = '<html lang="it"><script type="application/ld+json">{"address": {"addressCountry": "IT"}}</script>'
            return SimpleNamespace(status=200, text=body, content_type="text/html")

    st = _store("woocommerce")
    st.business_country = st.location_source = None
    observed = [{"title": "Scarpe running", "product_type": "Shoes"}] * 3
    assert catalog.update_catalog(st, Home([]), "https://x.example/", _Robots(), "bot", observed) is True
    assert (st.category_group, st.category) == ("Clothing & Shoes & Accessories", "Shoes")
    assert (st.business_country, st.location_source) == ("IT", "structured_data")
    assert st.published_products is None and st.first_product_at is None  # observed ≠ published catalog
    st.business_country, st.location_source = "CA", "shopify_meta"
    catalog.update_catalog(st, Home([]), "https://x.example/", _Robots(), "bot", observed)
    assert st.business_country == "CA"  # Shopify's own setting wins
