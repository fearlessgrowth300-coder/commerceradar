"""M6: activity classification, scheduling intervals, the monitoring tick, failure recovery,
and re-checks that update records without losing history."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app import tasks
from app.config import settings
from app.models import CrawlJob, DiscoveryRun, DiscoverySource, Domain, Store, StoreObservation
from app.services import monitoring, verification
from app.services.fetcher import SafeFetcher
from app.services.platforms import PlatformResult

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def host() -> str:
    return f"m{uuid.uuid4().hex[:10]}.example"


def make_store(db, **kw) -> Store:
    d = Domain(normalized_domain=host(), verification_status="confirmed_ecommerce")
    db.add(d)
    db.flush()
    s = Store(domain_id=d.id, ecommerce_status="confirmed_ecommerce", **{"consecutive_failures": 0, **kw})
    db.add(s)
    db.commit()
    return s


def days(dt: datetime) -> float:
    return (dt - NOW).total_seconds() / 86400


def obs(db, store, kind):
    q = select(StoreObservation).where(StoreObservation.store_id == store.id, StoreObservation.observation_type == kind)
    return db.scalars(q.order_by(StoreObservation.observed_at)).all()


# ---------- activity rules ----------


def test_new_store_becomes_active_without_counting_as_a_change(db):
    s = make_store(db, first_verified_at=NOW)
    monitoring.update_activity(db, s, "confirmed_ecommerce", 0.9, "resolved", NOW)
    assert (s.activity_status, s.activity_confidence, s.consecutive_failures) == ("active", 0.9, 0)
    assert s.last_changed_at is None
    assert 7 * 0.9 <= days(s.next_check_at) <= 7 * 1.1  # newly verified: ~weekly


def test_one_failure_never_closes_a_store(db):
    s = make_store(db, first_verified_at=NOW - timedelta(days=100), activity_status="active",
                   last_successful_check_at=NOW - timedelta(days=21))  # fmt: skip
    monitoring.update_activity(db, s, "temporarily_unreachable", 0.0, "error", NOW)
    assert s.activity_status == "recently_active" and s.consecutive_failures == 1
    assert 0.9 <= days(s.next_check_at) <= 1.1  # transient ⇒ retry tomorrow
    monitoring.update_activity(db, s, "uncertain", 0.0, "resolved", NOW)  # e.g. bot wall
    assert s.activity_status == "recently_active"
    assert 14 * 0.9 <= days(s.next_check_at) <= 14 * 1.1  # don't hammer a bot wall


def test_repeated_negative_evidence_marks_inactive(db):
    s = make_store(db, first_verified_at=NOW - timedelta(days=100), activity_status="active")
    for i in range(settings.inactive_after_negative_checks):
        assert s.activity_status != "inactive"
        monitoring.update_activity(db, s, "not_ecommerce", 0.9, "resolved", NOW + timedelta(days=3 * i))
    assert s.activity_status == "inactive" and "consecutive checks" in s.activity_evidence["rule"]
    db.commit()
    changes = [(o.observation_data["from"], o.observation_data["to"]) for o in obs(db, s, "activity_change")]
    assert changes == [("active", "recently_active"), ("recently_active", "inactive")]


def test_inconclusive_failures_need_time_as_well_as_count(db):
    s = make_store(db, first_verified_at=NOW - timedelta(days=200), activity_status="active",
                   last_successful_check_at=NOW - timedelta(days=30))  # fmt: skip
    for _ in range(settings.inactive_after_failed_checks + 2):
        monitoring.update_activity(db, s, "uncertain", 0.0, "resolved", NOW)
    assert s.activity_status == "recently_active"  # many failures, but it worked 30 days ago
    s.last_successful_check_at = NOW - timedelta(days=settings.inactive_min_days_since_success + 1)
    monitoring.update_activity(db, s, "uncertain", 0.0, "resolved", NOW)
    assert s.activity_status == "inactive" and s.activity_confidence == 0.5
    assert 60 * 0.9 <= days(s.next_check_at) <= 60 * 1.1


def test_recovery_resets_and_recently_changed_is_rechecked_sooner(db):
    s = make_store(db, first_verified_at=NOW - timedelta(days=200), activity_status="inactive",
                   consecutive_failures=0, last_failed_check_at=NOW - timedelta(days=60))  # fmt: skip
    monitoring.update_activity(db, s, "confirmed_ecommerce", 0.95, "resolved", NOW)
    assert s.activity_status == "active" and s.consecutive_failures == 0 and s.last_changed_at == NOW
    assert 2 * 0.9 <= days(s.next_check_at) <= 2 * 1.1  # just changed ⇒ every ~2 days
    s.last_changed_at = NOW - timedelta(days=60)
    monitoring.update_activity(db, s, "confirmed_ecommerce", 0.95, "resolved", NOW)
    assert 21 * 0.9 <= days(s.next_check_at) <= 21 * 1.1  # established active store


def test_failure_kinds():
    assert monitoring.failure_kind("confirmed_ecommerce", "resolved") is None
    assert monitoring.failure_kind("not_ecommerce", "resolved") == "negative"
    assert monitoring.failure_kind("temporarily_unreachable", "nxdomain") == "negative"
    assert monitoring.failure_kind("temporarily_unreachable", "error") == "transient"
    assert monitoring.failure_kind("uncertain", "resolved") == "inconclusive"


# ---------- re-check through apply_result keeps history ----------


def _check(db, store, status, platform=None):
    d = db.get(Domain, store.domain_id)
    job = CrawlJob(domain_id=d.id, target_domain=d.normalized_domain, job_type="verify", status="running")
    db.add(job)
    db.commit()
    verification.apply_result(
        db, job, verification.VerificationResult(status, 0.9, "resolved", {}, f"https://{d.normalized_domain}/", "S", platform)
    )


def test_recheck_updates_without_losing_history(db):
    s = make_store(db, first_verified_at=NOW, primary_platform="woocommerce")
    _check(db, s, "confirmed_ecommerce", PlatformResult("shopify", 0.9))
    _check(db, s, "not_ecommerce")
    db.refresh(s)
    assert s.ecommerce_status == "confirmed_ecommerce"  # one negative check doesn't rewrite what it was
    assert s.activity_status == "recently_active"
    assert [o.observation_data["status"] for o in obs(db, s, "verification")] == ["confirmed_ecommerce", "not_ecommerce"]
    assert [(o.observation_data["from"], o.observation_data["to"]) for o in obs(db, s, "platform_change")] == [("woocommerce", "shopify")]


# ---------- the tick ----------


@pytest.fixture
def big_limits(monkeypatch):
    for name in ("monitor_rechecks_per_tick", "monitor_backlog_per_tick", "monitor_candidate_rechecks_per_tick"):
        monkeypatch.setattr(settings, name, 100000)


def run_tick(db, now=None):
    calls = {"verify": [], "products": [], "runs": []}
    out = monitoring.tick(
        db, now or datetime.now(UTC),
        enqueue_verify=calls["verify"].append, enqueue_products=calls["products"].append,
        enqueue_run=lambda rid, seq: calls["runs"].append((rid, seq)),
    )  # fmt: skip
    return out, calls


def jobs_for(db, domain_id):
    return db.scalars(select(CrawlJob).where(CrawlJob.domain_id == domain_id, CrawlJob.job_type == "verify")).all()


def test_tick_queues_due_backlog_and_stale_candidates_once(db, big_limits):
    now = datetime.now(UTC)
    due = make_store(db, first_verified_at=now - timedelta(days=30), next_check_at=now - timedelta(hours=1))
    later = make_store(db, first_verified_at=now, next_check_at=now + timedelta(days=5))
    fresh = Domain(normalized_domain=host())
    stale = Domain(normalized_domain=host(), verification_status="uncertain", last_checked_at=now - timedelta(days=45))
    recent = Domain(normalized_domain=host(), verification_status="uncertain", last_checked_at=now - timedelta(days=2))
    db.add_all([fresh, stale, recent])
    db.commit()

    out, calls = run_tick(db, now)
    assert len(jobs_for(db, due.domain_id)) == 1 and not jobs_for(db, later.domain_id)
    assert len(jobs_for(db, fresh.id)) == 1 and len(jobs_for(db, stale.id)) == 1 and not jobs_for(db, recent.id)
    assert {j.id for d in (due.domain_id, fresh.id, stale.id) for j in jobs_for(db, d)} <= set(calls["verify"])

    run_tick(db, now)  # same moment again: everything is in flight ⇒ nothing duplicated
    assert len(jobs_for(db, due.domain_id)) == 1 and len(jobs_for(db, fresh.id)) == 1 and len(jobs_for(db, stale.id)) == 1


def test_tick_respects_per_tick_limits(db, monkeypatch, big_limits):
    for _ in range(3):
        db.add(Domain(normalized_domain=host()))
    db.commit()
    monkeypatch.setattr(settings, "monitor_backlog_per_tick", 2)
    out, _ = run_tick(db)
    assert out["backlog_verifications"] == 2


def test_tick_starts_scheduled_sources(db, big_limits):
    src = DiscoverySource(name=f"sched-{uuid.uuid4().hex[:6]}", source_type="crtsh",
                          configuration={"schedule_hours": 24, "queries": []}, state={})  # fmt: skip
    db.add(src)
    db.commit()
    out, calls = run_tick(db)
    assert src.name in out["sources_started"]
    out2, _ = run_tick(db)
    assert src.name not in out2["sources_started"]  # active / ran recently
    for run in db.scalars(select(DiscoveryRun).where(DiscoveryRun.status.in_(("queued", "running")))):
        run.status = "cancelled"  # don't leave scheduled runs active for other tests
    db.commit()


def test_stuck_jobs_are_resent_then_abandoned(db, big_limits):
    now = datetime.now(UTC)
    d = Domain(normalized_domain=host(), verification_status="pending")
    db.add(d)
    db.flush()
    old = now - timedelta(minutes=settings.stuck_after_minutes + 5)
    lost = CrawlJob(domain_id=d.id, target_domain=d.normalized_domain, job_type="verify", status="running",
                    attempts=1, scheduled_at=old, started_at=old)  # fmt: skip
    hopeless = CrawlJob(domain_id=d.id, target_domain=d.normalized_domain, job_type="products", status="queued",
                        attempts=settings.max_job_attempts, scheduled_at=old)  # fmt: skip
    db.add_all([lost, hopeless])
    db.commit()
    out, calls = run_tick(db, now)
    db.refresh(lost), db.refresh(hopeless)
    assert lost.status == "queued" and lost.id in calls["verify"]
    assert hopeless.status == "failed" and "abandoned" in hopeless.error_message


def test_stuck_discovery_run_is_resumed(db, big_limits):
    src = DiscoverySource(name=f"stuck-{uuid.uuid4().hex[:6]}", source_type="crtsh", configuration={}, state={})
    db.add(src)
    db.flush()
    old = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
    run = DiscoveryRun(source_id=src.id, status="running", started_at=datetime.now(UTC) - timedelta(hours=5),
                       cursor={"seq": 4, "touched": old})  # fmt: skip
    db.add(run)
    db.commit()
    out, calls = run_tick(db)
    assert (run.id, 4) in calls["runs"]
    run.status = "cancelled"
    db.commit()


# ---------- product monitoring hook + API ----------


def test_positive_check_queues_product_recheck(client, monkeypatch):
    h = host()
    page = httpx.Response(200, text=open(__file__.replace("test_monitoring.py", "fixtures/store_home.html"), encoding="utf-8").read(),
                          headers={"content-type": "text/html"})  # fmt: skip
    monkeypatch.setattr(tasks, "SafeFetcher", lambda: SafeFetcher(lambda x: ["93.184.216.34"], httpx.MockTransport(lambda r: page)))
    monkeypatch.setattr(settings, "auto_extract_products", True)
    queued = []
    monkeypatch.setattr(tasks, "_enqueue_products", queued.append)
    from app.api import v1

    monkeypatch.setattr(v1, "_enqueue", lambda j: None)
    job_id = client.post("/api/v1/discovery/domains", json={"domain": h}).json()["job"]["id"]
    assert tasks.verify_domain.apply(args=[job_id]).result == "confirmed_ecommerce"
    assert len(queued) == 1
    detail = client.get(f"/api/v1/domains/{client.get('/api/v1/domains', params={'q': h}).json()['items'][0]['id']}").json()
    assert detail["activity"]["activity_status"] == "active" and detail["activity"]["next_check_at"]


def test_monitoring_api(client, monkeypatch):
    status = client.get("/api/v1/monitoring/status").json()
    assert {"stores_due", "unverified_backlog", "jobs_in_flight", "activity", "per_tick"} <= set(status)
    sent = []
    monkeypatch.setattr(tasks.monitoring_tick, "delay", lambda: sent.append(1))
    assert client.post("/api/v1/monitoring/tick").status_code == 202 and sent == [1]
