"""M8: accounts, auth, tenancy, plans/limits, saved searches, alerts, exports, billing adapter,
admin and suppression."""

import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import cli, tasks
from app.config import settings
from app.main import app
from app.models import Alert, CrawlJob, Domain, Organization, Store, User
from app.services import accounts, billing, workspace


def email() -> str:
    return f"u{uuid.uuid4().hex[:10]}@tests.example"


def user_client(org: str | None = None) -> tuple[TestClient, str]:
    e = email()
    r = TestClient(app).post("/api/v1/auth/register", json={"email": e, "password": "a long password", "organization": org})
    assert r.status_code == 201, r.text
    return TestClient(app, headers={"Authorization": f"Bearer {r.json()['token']}"}), e


def make_store(db, host=None, **kw) -> Store:
    d = Domain(normalized_domain=host or f"t{uuid.uuid4().hex[:10]}.example", verification_status="confirmed_ecommerce")
    db.add(d)
    db.flush()
    s = Store(domain_id=d.id, **{"ecommerce_status": "confirmed_ecommerce", "activity_status": "active",
                                 "consecutive_failures": 0, "first_verified_at": datetime.now(UTC), **kw})  # fmt: skip
    db.add(s)
    db.commit()
    return s


# ---------- passwords, sessions, lockout ----------


def test_password_hashing():
    h = accounts.hash_password("correct horse battery")
    assert h.startswith("scrypt$") and "correct" not in h
    assert accounts.verify_password("correct horse battery", h)
    assert not accounts.verify_password("wrong", h)
    assert not accounts.verify_password("x", "garbage")
    assert accounts.hash_password("same") != accounts.hash_password("same")  # salted


def test_register_validation(db):
    for bad_email, pw in (("nope", "a long password"), (email(), "short")):
        with pytest.raises(accounts.AuthError):
            accounts.register(db, bad_email, pw, None, None)
    e = email()
    u = accounts.register(db, e, "a long password", "N", None)
    assert u.is_admin is False  # only the very first user (or the CLI) is admin
    with pytest.raises(accounts.AuthError, match="exists"):
        accounts.register(db, e.upper(), "a long password", None, None)


def test_login_lockout_logout_and_expiry(db):
    e = email()
    accounts.register(db, e, "a long password", None, None)
    for _ in range(accounts.MAX_FAILED):
        with pytest.raises(accounts.AuthError):
            accounts.login(db, e, "wrong password!")
    with pytest.raises(accounts.AuthError, match="too many"):
        accounts.login(db, e, "a long password")  # locked even with the right password
    later = datetime.now(UTC) + timedelta(minutes=accounts.LOCK_MINUTES + 1)
    token = accounts.login(db, e, "a long password", now=later)
    assert accounts.user_for_token(db, token, now=later).email == e
    assert accounts.user_for_token(db, token, now=later + timedelta(days=settings.session_days + 1)) is None
    accounts.logout(db, token)
    assert accounts.user_for_token(db, token, now=later) is None


def test_tokens_are_stored_hashed(db):
    from app.models import AuthSession

    e = email()
    accounts.register(db, e, "a long password", None, None)
    token = accounts.login(db, e, "a long password")
    assert db.scalar(select(AuthSession).where(AuthSession.token_hash == token)) is None
    assert db.scalar(select(AuthSession).where(AuthSession.token_hash == hashlib.sha256(token.encode()).hexdigest()))


# ---------- API protection ----------


def test_api_requires_session_and_admin_for_operations(anon, monkeypatch):
    assert anon.get("/api/v1/stores").status_code == 401
    assert anon.get("/api/v1/dashboard/overview").status_code == 401
    assert anon.get("/health").status_code in (200, 503)  # health stays public
    assert TestClient(app, headers={"Authorization": "Bearer forged"}).get("/api/v1/stores").status_code == 401

    member, _ = user_client()
    assert member.get("/api/v1/stores").status_code == 200
    assert member.get("/api/v1/auth/me").json()["is_admin"] is False
    from app.api import v1

    monkeypatch.setattr(v1, "_enqueue", lambda j: None)
    for method, path, body in (
        ("post", "/api/v1/discovery/domains", {"domain": "x-shop.example"}),
        ("post", "/api/v1/monitoring/tick", None),
        ("get", "/api/v1/admin/overview", None),
        ("post", "/api/v1/admin/suppressions", {"domain": "x.example"}),
    ):
        assert getattr(member, method)(path, **({"json": body} if body else {})).status_code == 403, path


def test_login_and_logout_endpoints(anon):
    e = email()
    assert anon.post("/api/v1/auth/register", json={"email": e, "password": "a long password"}).status_code == 201
    assert anon.post("/api/v1/auth/login", json={"email": e, "password": "nope nope nope"}).status_code == 401
    token = anon.post("/api/v1/auth/login", json={"email": e, "password": "a long password"}).json()["token"]
    c = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    assert c.get("/api/v1/auth/me").json()["email"] == e
    assert c.post("/api/v1/auth/logout").status_code == 204
    assert c.get("/api/v1/auth/me").status_code == 401


# ---------- tenancy, plans, saved searches, alerts ----------


def test_saved_searches_are_tenant_isolated_and_plan_limited(db):
    a, _ = user_client("Org A")
    b, _ = user_client("Org B")
    s = a.post("/api/v1/searches", json={"name": "Shopify", "filters": {"platform": "shopify", "junk": "x"}}).json()
    assert s["filters"] == {"platform": "shopify"}  # unknown keys dropped
    assert [x["id"] for x in b.get("/api/v1/searches").json()] == []
    assert b.delete(f"/api/v1/searches/{s['id']}").status_code == 404  # other tenant's row looks nonexistent
    for i in range(billing.PLANS["free"]["saved_searches"] - 1):
        assert a.post("/api/v1/searches", json={"name": f"s{i}"}).status_code == 201
    assert a.post("/api/v1/searches", json={"name": "one too many"}).status_code == 402
    assert b.post("/api/v1/searches", json={"name": "alerts", "alerts_enabled": True}).status_code == 402  # not on free
    assert a.post("/api/v1/searches", json={"name": "x", "filters": {"verified_from": "not-a-date"}}).status_code in (402, 422)


def test_alerts_for_newly_verified_matching_stores(db, client):
    member, e = user_client()
    org_id = member.get("/api/v1/auth/me").json()["organization"]["id"]
    assert client.put(f"/api/v1/admin/organizations/{org_id}/plan", json={"plan": "pro"}).status_code == 200
    tag = uuid.uuid4().hex[:6]
    search = member.post("/api/v1/searches", json={"name": "new", "filters": {"q": tag}, "alerts_enabled": True}).json()
    make_store(db, host=f"old{tag}.example", first_verified_at=datetime.now(UTC) - timedelta(days=5))  # before the search
    new = make_store(db, host=f"new{tag}.example")
    assert workspace.run_alerts(db, datetime.now(UTC)) >= 1
    workspace.run_alerts(db, datetime.now(UTC))  # idempotent
    alerts = member.get("/api/v1/alerts").json()
    assert [a["domain"] for a in alerts] == [f"new{tag}.example"] and alerts[0]["search"] == "new"
    assert member.post("/api/v1/alerts/read").json()["marked"] == 1
    assert member.get("/api/v1/alerts", params={"unread": True}).json() == []
    assert db.scalars(select(Alert).where(Alert.store_id == new.id)).all()


# ---------- exports ----------


def test_export_csv_quota_and_injection_guard(db):
    member, _ = user_client()
    tag = uuid.uuid4().hex[:6]
    make_store(db, host=f"csv{tag}.example", store_name="=HYPERLINK(evil)", primary_platform="shopify")
    r = member.get("/api/v1/exports/stores.csv", params={"q": tag})
    assert r.status_code == 200 and r.headers["x-rows"] == "1"
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("domain,store_name,primary_platform")
    assert f"csv{tag}.example,'=HYPERLINK(evil),shopify" in lines[1]
    for _ in range(billing.PLANS["free"]["exports_per_month"] - 1):
        assert member.get("/api/v1/exports/stores.csv", params={"q": tag}).status_code == 200
    assert member.get("/api/v1/exports/stores.csv", params={"q": tag}).status_code == 402
    assert member.get("/api/v1/account").json()["usage"]["exports"] == billing.PLANS["free"]["exports_per_month"]


def test_export_limit_and_domains_only(db, client):
    tag = uuid.uuid4().hex[:6]
    for i in range(3):
        make_store(db, host=f"lim{i}{tag}.example", primary_platform="shopify")
    r = client.get("/api/v1/exports/stores.csv", params={"q": tag, "limit": 2, "columns": "domains"})  # admin: no plan cap
    assert r.status_code == 200 and r.headers["x-rows"] == "2"
    lines = r.text.strip().splitlines()
    assert lines[0] == "domain" and len(lines) == 3 and all(l.endswith(f"{tag}.example") for l in lines[1:])
    full = client.get("/api/v1/exports/stores.csv", params={"q": tag}).text.splitlines()[0]
    assert "business_country" in full
    assert client.get("/api/v1/exports/stores.csv", params={"q": tag, "limit": "x"}).status_code == 422


def test_export_parts_split_rows_without_overlap(db, client, monkeypatch):
    from app.services import workspace

    monkeypatch.setattr(workspace, "PART_ROWS", {"domains": 2, "full": 2})
    tag = uuid.uuid4().hex[:6]
    for i in range(5):
        make_store(db, host=f"part{i}{tag}.example", primary_platform="shopify")
    get = lambda **p: client.get("/api/v1/exports/stores.csv", params={"q": tag, "columns": "domains", **p}).text.splitlines()[1:]  # noqa: E731
    everything = get()
    assert len(everything) == 5
    parts = [get(part=k) for k in (1, 2, 3, 4)]
    assert [len(p) for p in parts] == [2, 2, 1, 0]
    assert sum(parts, []) == everything  # same order, no gaps, no duplicates
    assert [len(get(part=k, limit=3)) for k in (1, 2)] == [2, 1]


# ---------- suppression (opt-out) ----------


def test_suppression_hides_and_stops_crawling(db, client, monkeypatch):
    s = make_store(db)
    host = db.get(Domain, s.domain_id).normalized_domain
    job = CrawlJob(domain_id=s.domain_id, target_domain=host, job_type="verify", status="queued")
    db.add(job)
    db.commit()
    assert client.post("/api/v1/admin/suppressions", json={"domain": f"https://www.{host}/", "reason": "owner asked"}).status_code == 201
    db.refresh(job)
    assert job.status == "failed" and "suppressed" in job.error_message
    assert client.get(f"/api/v1/stores/{s.id}").status_code == 404
    assert all(r["id"] != str(s.id) for r in client.get("/api/v1/stores", params={"q": host}).json()["items"])
    from app.api import v1

    monkeypatch.setattr(v1, "_enqueue", lambda j: None)
    assert client.post("/api/v1/discovery/domains", json={"domain": host}).status_code == 422
    j2 = CrawlJob(domain_id=s.domain_id, target_domain=host, job_type="verify", status="queued")
    db.add(j2)
    db.commit()
    assert tasks.verify_domain.apply(args=[str(j2.id)]).result == "suppressed"
    assert host in [x["domain"] for x in client.get("/api/v1/admin/suppressions").json()]
    assert client.delete(f"/api/v1/admin/suppressions/{host}").status_code == 204
    assert client.get(f"/api/v1/stores/{s.id}").status_code == 200


# ---------- billing adapter ----------


def sign(payload: bytes, secret: str, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    return f"t={ts},v1={hmac.new(secret.encode(), f'{ts}.'.encode() + payload, hashlib.sha256).hexdigest()}"


def test_webhook_signature_verification():
    body = json.dumps({"type": "x"}).encode()
    assert billing.verify_webhook(body, sign(body, "whsec"), "whsec") == {"type": "x"}
    for header in (sign(body, "other"), sign(body, "whsec", ts=int(time.time()) - 3600), "garbage", ""):
        with pytest.raises(ValueError):
            billing.verify_webhook(body, header, "whsec")


def test_billing_not_operational_without_keys(client):
    assert billing.configured() is False
    assert client.post("/api/v1/billing/checkout", json={"plan": "pro"}).status_code == 501
    assert client.post("/api/v1/billing/webhook", content=b"{}").status_code == 501
    assert client.get("/api/v1/account").json()["billing_configured"] is False


def test_checkout_and_events_with_mocked_stripe(db, monkeypatch):
    for k, v in {"stripe_secret_key": "sk_test", "stripe_webhook_secret": "whsec", "stripe_price_pro": "price_pro"}.items():
        monkeypatch.setattr(settings, k, v)
    org = Organization(name="Paying", plan="free")
    db.add(org)
    db.commit()
    sent = {}

    def stripe(request: httpx.Request):
        sent["form"] = dict(httpx.QueryParams(request.content.decode()))
        sent["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"url": "https://checkout.stripe.test/s/1"})

    url = billing.checkout_url(org, "pro", client=httpx.Client(transport=httpx.MockTransport(stripe)))
    assert url == "https://checkout.stripe.test/s/1"
    assert sent["form"]["line_items[0][price]"] == "price_pro" and sent["form"]["client_reference_id"] == str(org.id)
    done = {"type": "checkout.session.completed",
            "data": {"object": {"client_reference_id": str(org.id), "customer": "cus_1", "metadata": {"plan": "pro"}}}}  # fmt: skip
    assert billing.apply_event(db, done) == "plan_activated" and billing.apply_event(db, done) == "plan_activated"
    db.refresh(org)
    assert (org.plan, org.billing_customer_id) == ("pro", "cus_1")
    billing.apply_event(db, {"type": "customer.subscription.deleted", "data": {"object": {"customer": "cus_1", "status": "canceled"}}})
    db.refresh(org)
    assert org.plan == "free" and org.subscription_status == "canceled"


# ---------- admin ----------


def test_admin_overview_plan_and_user_management(db, client):
    member, e = user_client("Managed")
    ov = client.get("/api/v1/admin/overview").json()
    assert any(u["email"] == e for u in ov["users"]) and "suppressed_domains" in ov
    uid = next(u["id"] for u in ov["users"] if u["email"] == e)
    assert client.put(f"/api/v1/admin/organizations/{uuid.uuid4()}/plan", json={"plan": "pro"}).status_code == 404
    org_id = member.get("/api/v1/auth/me").json()["organization"]["id"]
    assert client.put(f"/api/v1/admin/organizations/{org_id}/plan", json={"plan": "platinum"}).status_code == 422
    assert client.patch(f"/api/v1/admin/users/{uid}", json={"is_active": False}).status_code == 200
    assert member.get("/api/v1/auth/me").status_code == 401  # deactivated ⇒ sessions stop working
    me = client.get("/api/v1/auth/me").json()["id"]
    assert client.patch(f"/api/v1/admin/users/{me}", json={"is_admin": False}).status_code == 409


def test_cli_create_admin(db, capsys):
    e = email()
    assert cli.main(["create-admin", e]) == 0
    out = capsys.readouterr().out
    password = out.split("password: ")[1].strip()
    assert db.scalar(select(User).where(User.email == e)).is_admin
    assert accounts.login(db, e, password)
    assert cli.main(["create-admin", e]) == 1  # duplicate


def test_new_only_downloads_never_repeat_a_store(db, client, monkeypatch):
    from app.services import workspace

    tag = uuid.uuid4().hex[:6]
    for i in range(5):
        make_store(db, host=f"new{i}{tag}.example", primary_platform="shopify")
    get = lambda **p: client.get("/api/v1/exports/stores.csv", params={"q": tag, "columns": "domains", **p}).text.splitlines()[1:]  # noqa: E731
    first = get(new_only=1, limit=2)
    second = get(new_only=1, limit=2)
    third = get(new_only=1, limit=2)
    assert [len(first), len(second), len(third)] == [2, 2, 1]
    assert len(set(first + second + third)) == 5  # no store twice
    assert get(new_only=1) == []  # everything downloaded
    assert len(get()) == 5  # without the box ticked, everything is still available
    assert client.get("/api/v1/stores", params={"q": tag, "new_only": True}).json()["total"] == 0

    monkeypatch.setattr(workspace, "PART_ROWS", {"domains": 2, "full": 2})
    tag2 = uuid.uuid4().hex[:6]
    for i in range(5):
        make_store(db, host=f"np{i}{tag2}.example", primary_platform="shopify")
    parts = [client.get("/api/v1/exports/stores.csv", params={"q": tag2, "columns": "domains", "new_only": 1, "limit": 5, "part": k}).text.splitlines()[1:] for k in (1, 2, 3)]
    assert [len(p) for p in parts] == [2, 2, 1] and len(set(sum(parts, []))) == 5
