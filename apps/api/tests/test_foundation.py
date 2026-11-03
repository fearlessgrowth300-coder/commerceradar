import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.exc import IntegrityError

from app import main
from app.db import engine
from app.models import Base, Domain, Store
from app.worker import celery_app, ping


def test_migrations_match_models():
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_health_ok_when_all_services_up(client, monkeypatch):
    class FakeRedis:
        def ping(self):
            return True

    monkeypatch.setattr(main.redis, "from_url", lambda *a, **k: FakeRedis())
    monkeypatch.setattr(main.celery_app.control, "ping", lambda timeout: [{"worker@x": {"ok": "pong"}}])
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok", "worker": "ok"}}


def test_health_degraded_when_redis_down(client, monkeypatch):
    monkeypatch.setattr(main.settings, "redis_url", "redis://127.0.0.1:1/0")  # closed port
    r = client.get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"].startswith("error")
    assert body["checks"]["worker"] == "unknown"


def test_overview_counts_real_rows(client, db):
    before = client.get("/api/v1/dashboard/overview").json()
    d = Domain(normalized_domain="overview-test.example")
    db.add(d)
    db.flush()
    db.add(Store(domain_id=d.id, ecommerce_status="confirmed", activity_status="active"))
    db.commit()
    after = client.get("/api/v1/dashboard/overview").json()
    assert after["candidate_domains"] == before["candidate_domains"] + 1
    assert after["verified_stores"] == before["verified_stores"] + 1
    assert after["active_stores"] == before["active_stores"] + 1
    assert after["inactive_stores"] == before["inactive_stores"]


def test_duplicate_normalized_domain_rejected(db):
    db.add(Domain(normalized_domain="dupe.example"))
    db.commit()
    db.add(Domain(normalized_domain="dupe.example"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_worker_ping_task(monkeypatch):
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    assert ping.delay().get() == "pong"
