"""CZDS zone-file source: daily diff + Shopify DNS filter. No network: mocked CZDS API and resolver."""

import gzip

import httpx
import pytest

from app.config import settings
from app.sources import Ctx, SourceError, czds

API = "https://czds-api.icann.org/czds/downloads"


def zone(*names: str) -> bytes:
    lines = ["shop.\t86400\tin\tsoa\tns.example. h.example. 1 1 1 1 1", "shop.\t86400\tin\tns\ta.nic.shop."]
    for n in names:
        lines += [f"{n}.shop.\t3600\tin\tns\tns1.host.example.", f"{n}.SHOP.\t3600\tIN\tNS\tns2.host.example."]
    lines.append("ns1.deep.sub.shop.\t3600\tin\ta\t1.2.3.4")  # glue / deeper names are ignored
    return gzip.compress("\n".join(lines).encode())


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ZONE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "czds_username", "u")
    monkeypatch.setattr(settings, "czds_password", "p")
    ips = {"newshopify.shop": ["23.227.38.65"], "newother.shop": ["104.21.1.1"]}
    monkeypatch.setattr(czds, "apex_ips", lambda n: ips.get(n, []))
    served = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/authenticate":
            return httpx.Response(200, json={"accessToken": "t"})
        assert req.headers["authorization"] == "Bearer t"
        if req.url.path == "/czds/downloads/links":
            return httpx.Response(200, json=[f"{API}/shop.zone", "https://evil.example/czds/downloads/store.zone"])
        return httpx.Response(200, content=served["zone"])

    return served, httpx.Client(transport=httpx.MockTransport(handler))


def run(day, client):
    src, cfg, state, cursor, found = czds.Czds(), {"tlds": ["shop", "store"], "dns_per_batch": 2}, {}, {"day": day}, []
    notes = []
    while cursor is not None:
        b = src.fetch_batch(cfg, cursor, state, Ctx(fetcher=None, db=None), client=client)
        found += b.found
        notes.append(b.note)
        cursor = b.cursor
    return found, notes, state


def test_first_day_is_baseline_then_only_new_shopify_names(env, tmp_path):
    served, client = env
    served["zone"] = zone("old1", "old2", "newother")
    found, notes, state = run("2032-01-01", client)
    assert found == [] and state["last"]["shop"]["baseline"] is True
    assert ".store: not approved for this account (skipped)" in notes  # foreign-host link was ignored

    served["zone"] = zone("old1", "old2", "newother", "newshopify", "newnothing")
    found, notes, state = run("2032-01-02", client)
    assert state["last"]["shop"] == {"day": "2032-01-02", "new": 2, "baseline": False}  # newother existed yesterday
    assert [(f.url, f.ref) for f in found] == [("https://newshopify.shop/", "czds:shop:2032-01-02:23.227.38.65")]
    left = sorted(p.name for p in (tmp_path / "shop").iterdir())
    assert left == ["domains-2032-01-02.txt", "new-2032-01-02.txt"]  # raw zone + older lists deleted


def test_missing_credentials_is_a_permanent_error(env, monkeypatch):
    monkeypatch.setattr(settings, "czds_password", "")
    with pytest.raises(SourceError):
        run("2032-02-01", env[1])
