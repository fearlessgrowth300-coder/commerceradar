"""Celery app. Same codebase as the API, started with a different command,
so workers can later run on separate machines against the same Postgres/Redis."""

from celery import Celery

from app.config import settings

celery_app = Celery("commerceradar", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_acks_late=True,  # a crashed worker's task is redelivered, so tasks must be idempotent
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=240,
    task_time_limit=300,
    result_expires=86400,
    broker_connection_retry_on_startup=True,
    include=["app.tasks"],
    # One queue per task category so each can get its own worker pool later.
    task_routes={"verification.*": {"queue": "verification"}},
)


@celery_app.task(name="system.ping")
def ping() -> str:
    return "pong"
