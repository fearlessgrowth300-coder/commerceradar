"""Tests run against SQLite by default (no services needed).
Set TEST_DATABASE_URL to run against Postgres (docker: `make test`)."""

import os
import tempfile

_db = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["DATABASE_URL"] = _db
os.environ["AUTO_EXTRACT_PRODUCTS"] = "false"  # tests enqueue explicitly; never via the real broker

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def migrated():
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    command.downgrade(cfg, "base")  # clean slate when reusing a Postgres test DB
    command.upgrade(cfg, "head")
    yield cfg


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session
        session.rollback()


@pytest.fixture
def client():
    return TestClient(app)
