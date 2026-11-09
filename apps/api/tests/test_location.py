from types import SimpleNamespace

from app.services.products import parse_shop_meta, update_location


def test_parse_shop_meta_keeps_only_valid_country():
    assert parse_shop_meta('{"country":"us","province":"Ohio","city":"Dayton"}') == {"country": "US", "region": "Ohio", "city": "Dayton"}
    assert parse_shop_meta('{"country":"","city":"x"}') is None
    assert parse_shop_meta('{"country":"USA"}') is None
    assert parse_shop_meta("<html>password page</html>") is None
    assert parse_shop_meta("[1,2]") is None


class _Robots:
    def __init__(self, allow):
        self.allow = allow

    def can_fetch(self, ua, url):
        return self.allow


class _Fetcher:
    def __init__(self, status, text):
        self.calls, self.status, self.text = [], status, text

    def fetch(self, url):
        self.calls.append(url)
        return SimpleNamespace(status=self.status, text=self.text)


def test_update_location_respects_robots_and_sets_fields():
    store = SimpleNamespace(business_country=None, business_region=None, business_city=None, location_source=None, location_checked_at=None)
    f = _Fetcher(200, '{"country":"CA","province":"Ontario","city":"Aurora"}')
    assert update_location(store, f, "https://shop.example/", _Robots(False), "bot") is False
    assert f.calls == [] and store.location_checked_at is None  # disallowed: not even requested
    assert update_location(store, f, "https://shop.example/", _Robots(True), "bot") is True
    assert f.calls == ["https://shop.example/meta.json"]
    assert (store.business_country, store.business_region, store.business_city, store.location_source) == ("CA", "Ontario", "Aurora", "shopify_meta")


def test_store_list_country_filter(client):
    assert client.get("/api/v1/stores", params={"country": "us"}).status_code == 200
    assert client.get("/api/v1/stores", params={"country": "USA"}).status_code == 422
