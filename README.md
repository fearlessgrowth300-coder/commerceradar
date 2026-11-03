# CommerceRadar

Ecommerce store discovery and intelligence platform. Spec: [docs/spec.md](docs/spec.md).

## Run (Docker)

```sh
cp .env.example .env
docker compose up -d --build     # or: make up
```

| Service | URL |
|---|---|
| Dashboard | http://localhost:3000 |
| API | http://localhost:8000 (docs at `/docs`) |
| Health | http://localhost:8000/health |
| Postgres / Redis | 127.0.0.1:5432 / 127.0.0.1:6379 |

The API container runs `alembic upgrade head` on start. Tests against Postgres: `make test` (uses the separate `<db>_test` database).

## Run tests without Docker

```sh
cd apps/api
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # .venv/bin on Linux/macOS
.venv/Scripts/python -m pytest -q                                 # SQLite, no services needed
```

## Architecture (M1)

- **apps/api**: FastAPI, SQLAlchemy 2, Alembic, Celery (Redis broker). The API and the worker share one package (`app`) and one image, and start with different commands.
- **apps/web**: Next.js (App Router), TypeScript, Tailwind. It renders on the server and calls the API only.
- **Postgres 17, Redis 7**: Redis runs with AOF and `noeviction` so queued jobs are never silently dropped.
