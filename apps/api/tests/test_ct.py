"""CT log source: SAN parsing, log positions, Shopify DNS filter. No network: fake fetcher + resolver."""

import base64
import json
from types import SimpleNamespace

import pytest

from app.models import Domain
from app.sources import Ctx, SourceError, ct

LOG = "https://ct.example/log/"


def der_with_sans(*names: str, critical: bool = False) -> bytes:
    gn = b"".join(b"\x82" + bytes([len(n)]) + n.encode() for n in names)
    seq = b"\x30" + bytes([len(gn)]) + gn
    ext = ct.SAN_OID + (b"\x01\x01\xff" if critical else b"") + b"\x04" + bytes([len(seq)]) + seq
    return b"\x30\x00junk" + ext + b"tail"


def x509_entry(*names: str) -> dict:
    cert = der_with_sans(*names)
    leaf = b"\x00\x00" + b"\x00" * 8 + b"\x00\x00" + len(cert).to_bytes(3, "big") + cert + b"\x00\x00"
    return {"leaf_input": base64.b64encode(leaf).decode(), "extra_data": ""}


def precert_entry(*names: str) -> dict:
    cert = der_with_sans(*names, critical=True)
    leaf = b"\x00\x00" + b"\x00" * 8 + b"\x00\x01" + b"\x00" * 32 + b"\x00\x00\x05tbs.."
    extra = len(cert).to_bytes(3, "big") + cert + b"\x00\x00\x00"
    return {"leaf_input": base64.b64encode(leaf).decode(), "extra_data": base64.b64encode(extra).decode()}


def test_dns_names_from_x509_and_precert_entries():
    assert ct.dns_names(ct.entry_cert(x509_entry("shop.example.com", "www.shop.example.com"))) == ["shop.example.com", "www.shop.example.com"]
    assert ct.dns_names(ct.entry_cert(precert_entry("*.boutique.co.uk"))) == ["*.boutique.co.uk"]
    assert ct.dns_names(b"no extension here") == []
    assert ct.domains_of([x509_entry("www.a.com", "a.com", "x.myshopify.com"), precert_entry("*.b.co.uk")]) == ["a.com", "b.co.uk"]


class FakeLog:
    def __init__(self, size, entries):
        self.size, self.entries, self.calls = size, entries, []

    def fetch(self, url, headers=None):
        self.calls.append(url)
        if url.endswith("get-sth"):
            return SimpleNamespace(status=200, text=json.dumps({"tree_size": self.size}))
        q = dict(p.split("=") for p in url.split("?")[1].split("&"))
        start, end = int(q["start"]), int(q["end"])
        if start >= len(self.entries):
            return SimpleNamespace(status=400, text="{}")
        return SimpleNamespace(status=200, text=json.dumps({"entries": self.entries[start : min(end + 1, start + 2)]}))  # log caps pages at 2


@pytest.fixture
def shopify_dns(monkeypatch):
    ips = {"newstore.com": ["23.227.38.65"], "ctknown.com": ["23.227.38.66"], "other.com": ["104.21.1.1"]}
    monkeypatch.setattr("app.sources.czds.apex_ips", lambda n: ips.get(n, []))


def test_first_run_starts_at_log_end_then_reads_forward(db, shopify_dns):
    log = FakeLog(size=3, entries=[x509_entry("old.com")] * 3 + [x509_entry("newstore.com"), x509_entry("www.ctknown.com"), x509_entry("other.com")])
    db.add(Domain(normalized_domain="ctknown.com"))
    db.flush()
    cfg, state = {"logs": [LOG], "pages_per_batch": 5, "page_size": 2}, {}
    b = ct.CertTransparency().fetch_batch(cfg, {}, state, Ctx(log, db))
    assert state["positions"][LOG] == 6  # history (0..2) skipped, 3 new entries read
    assert [f.url for f in b.found] == ["https://newstore.com/"]  # ctknown.com already a domain, other.com not Shopify
    assert b.cursor is None and "caught up" in b.note
    b = ct.CertTransparency().fetch_batch(cfg, {}, state, Ctx(log, db))
    assert b.found == [] and state["positions"][LOG] == 6


def test_full_pages_continue_the_run(db, shopify_dns):
    log = FakeLog(size=0, entries=[x509_entry("newstore.com")] * 10)
    state = {"positions": {LOG: 0}}
    b = ct.CertTransparency().fetch_batch({"logs": [LOG], "pages_per_batch": 2, "page_size": 2}, {}, state, Ctx(log, db))
    assert state["positions"][LOG] == 4 and b.cursor == {"n": 1}  # not caught up: next batch continues


def test_requires_logs(db):
    with pytest.raises(SourceError):
        ct.CertTransparency().fetch_batch({}, {}, {}, Ctx(FakeLog(0, []), db))


def test_hourly_data_limit_stops_the_run(db):
    from datetime import UTC, datetime

    log = FakeLog(size=0, entries=[x509_entry("newstore.com")] * 10)
    state = {"positions": {LOG: 0}, "usage": {"hour": datetime.now(UTC).strftime("%Y-%m-%dT%H"), "mb": 5.0}}
    b = ct.CertTransparency().fetch_batch({"logs": [LOG], "max_mb_per_hour": 5}, {}, state, Ctx(log, db))
    assert b.cursor is None and "limit" in b.note and log.calls == []
    state["usage"] = {"hour": "2000-01-01T00", "mb": 99.0}  # a new hour resets the budget
    ct.CertTransparency().fetch_batch({"logs": [LOG], "max_mb_per_hour": 5, "pages_per_batch": 1}, {}, state, Ctx(log, db))
    assert state["usage"]["mb"] > 0 and state["usage"]["mb"] < 5 and state["positions"][LOG] == 2


def test_far_behind_jumps_to_newest(db, shopify_dns):
    log = FakeLog(size=100, entries=[x509_entry("old.com")] * 100 + [x509_entry("newstore.com")])
    state = {"positions": {LOG: 0}}
    b = ct.CertTransparency().fetch_batch({"logs": [LOG], "max_lag": 10}, {}, state, Ctx(log, db))
    assert "skipped 100" in b.note and [f.url for f in b.found] == ["https://newstore.com/"]
