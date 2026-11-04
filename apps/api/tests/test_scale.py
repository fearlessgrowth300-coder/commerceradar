"""M9: measured metrics, adaptive backlog budget, evidence retention, scale indexes."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select

from app.config import settings
from app.db import SessionLocal, engine
from app.main import app
from app.models import AuthSession, CrawlJob, DiscoveredUrl, DiscoverySource, Domain, User
from app.services import metrics, monitoring

IN_FLIGHT = ("queued", "running", "retrying")


def domain(db, **kw) -> Domain:
    d = Domain(normalized_domain=f"m9{uuid.uuid4().hex[:10]}.example", **kw)
    db.add(d)
    db.commit()
    return d


def job(db, d, *, done: datetime, secs: float, status="succeeded", attempts=1, result=None) -> CrawlJob:
    j = CrawlJob(domain_id=d.id, target_domain=d.normalized_domain, job_type="verify", status=status, attempts=attempts,
                 started_at=done - timedelta(seconds=secs), completed_at=done, result=result)  # fmt: skip
    db.add(j)
    db.commit()
    return j


def test_percentiles():
    assert metrics._pct([], 0.95) is None
    assert metrics._pct([7.0], 0.95) == 7.0
    assert 50 <= metrics._pct([float(i) for i in range(1, 101)], 0.5) <= 51
    assert 95 <= metrics._pct([float(i) for i in range(1, 101)], 0.95) <= 96


def test_job_stats_from_records(db):
    future = datetime.now(UTC) + timedelta(days=400)  # isolated window: only these rows
    d = domain(db)
    for s in (10, 20, 30, 40):
        job(db, d, done=future, secs=s)
    job(db, d, done=future, secs=5, status="failed", attempts=3)
    st = metrics.job_stats(db, future + timedelta(seconds=1), hours=1)["verify"]
    assert (st["completed"], st["succeeded"], st["failed"], st["retried"]) == (5, 4, 1, 1)
    assert st["avg_seconds"] == 25.0 and st["per_hour"] == 5.0


def test_source_yield(db):
    src = DiscoverySource(name=f"yield-{uuid.uuid4().hex[:6]}", source_type="crtsh", configuration={}, state={})
    db.add(src)
    db.flush()
    for status in ("confirmed_ecommerce", "not_ecommerce", "pending", "pending"):
        d = domain(db, verification_status=status)
        db.add(DiscoveredUrl(normalized_url=f"https://{d.normalized_domain}/", hostname=d.normalized_domain,
                             source_id=src.id, domain_id=d.id))  # fmt: skip
    db.commit()
    row = next(r for r in metrics.source_yield(db) if r["source"] == src.name)
    assert row == {"source": src.name, "candidates": 4, "verified": 2, "stores": 1, "store_rate": 0.5}


def test_metrics_endpoint_admin_only(client):
    snap = client.get("/api/v1/admin/metrics").json()
    assert {"jobs", "verification", "source_yield", "storage", "queues"} <= set(snap)
    assert {"pending_backlog", "measured_per_hour", "backlog_eta_hours"} <= set(snap["verification"])
    e = f"m9{uuid.uuid4().hex[:8]}@tests.example"
    tok = TestClient(app).post("/api/v1/auth/register", json={"email": e, "password": "a long password"}).json()["token"]
    assert TestClient(app, headers={"Authorization": f"Bearer {tok}"}).get("/api/v1/admin/metrics").status_code == 403


def test_backlog_budget_adapts_to_queue_depth(db, monkeypatch):
    for name in ("monitor_rechecks_per_tick", "monitor_candidate_rechecks_per_tick"):
        monkeypatch.setattr(settings, name, 0)
    monkeypatch.setattr(settings, "monitor_backlog_per_tick", 100000)
    for _ in range(3):
        domain(db)

    def tick():
        return monitoring.tick(db, datetime.now(UTC), lambda j: None, lambda j: None, lambda r, s: None)

    in_flight = db.scalar(select(func.count()).select_from(CrawlJob).where(CrawlJob.status.in_(IN_FLIGHT)))
    monkeypatch.setattr(settings, "monitor_max_in_flight", in_flight)  # queue already "full"
    assert tick()["backlog_verifications"] == 0
    monkeypatch.setattr(settings, "monitor_max_in_flight", in_flight + 2)
    assert tick()["backlog_verifications"] == 2  # tops up to the cap, no further


def test_compaction_keeps_latest_and_recent_evidence(db):
    now = datetime.now(UTC)
    d = domain(db)
    big = {"status": "confirmed_ecommerce", "confidence": 0.9, "resolution": "resolved",
           "evidence": {"score": {"points": 9}, "homepage": {"signals": {"x": "y" * 500}}},
           "platform": {"platform": "shopify", "confidence": 1.0, "evidence": {"lots": "of data"}}}  # fmt: skip
    old = job(db, d, done=now - timedelta(days=200), secs=5, result=big)
    latest = job(db, d, done=now - timedelta(days=150), secs=5, result=big)  # old but still the newest check
    other = domain(db)
    solo = job(db, other, done=now - timedelta(days=300), secs=5, result=big)  # only check for its domain
    u = User(email=f"m9{uuid.uuid4().hex[:8]}@tests.example", password_hash="x", failed_logins=0)
    db.add(u)
    db.flush()
    db.add(AuthSession(user_id=u.id, token_hash=uuid.uuid4().hex * 2, expires_at=now - timedelta(days=1)))
    db.commit()

    out = monitoring.compact_history(db, now)
    assert out["evidence_compacted"] >= 1 and out["sessions_expired"] >= 1
    for j in (old, latest, solo):
        db.refresh(j)
    assert old.result["evidence"]["compacted"] is True and old.result["evidence"]["score"] == {"points": 9}
    assert old.result["status"] == "confirmed_ecommerce" and old.result["platform"] == {"platform": "shopify", "confidence": 1.0}
    assert "homepage" in latest.result["evidence"] and "homepage" in solo.result["evidence"]
    assert monitoring.compact_history(db, now)["evidence_compacted"] == 0  # idempotent


def test_scale_indexes_exist():
    names = {i["name"] for i in inspect(engine).get_indexes("domains")} | {i["name"] for i in inspect(engine).get_indexes("crawl_jobs")}
    assert {"ix_domains_pending_first_seen", "ix_crawl_jobs_domain_type_status"} <= names
    with SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(Domain)) is not None
