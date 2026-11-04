import redis
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api import account, v1
from app.config import settings
from app.db import get_db
from app.worker import celery_app

app = FastAPI(title="CommerceRadar API", version="0.1.0")
app.include_router(account.public)
app.include_router(account.router)
app.include_router(v1.router)


def _check(fn) -> str:
    try:
        return fn()
    except Exception as e:  # report, never crash the health endpoint
        return f"error: {type(e).__name__}"


@app.get("/health")
def health(db: Session = Depends(get_db)):
    checks = {"database": _check(lambda: db.execute(text("select 1")) and "ok")}
    checks["redis"] = _check(lambda: redis.from_url(settings.redis_url, socket_timeout=2).ping() and "ok")
    # Only ask workers when Redis is up; otherwise the control ping blocks on reconnects.
    checks["worker"] = (
        _check(lambda: "ok" if celery_app.control.ping(timeout=1) else "no workers")
        if checks["redis"] == "ok"
        else "unknown"
    )
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)
