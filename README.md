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

**First login:** create a platform admin (prints a random password once):
`docker compose exec api python -m app.cli create-admin you@example.com`. Every API route except
`/health`, register/login and the Stripe webhook needs a session.

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

## What's built (milestones 1–9)

| Area | Where |
|---|---|
| Domain normalization, SSRF-safe fetcher, robots-aware ecommerce verification | `app/services/{domains,fetcher,verification}.py`, `app/security/ssrf.py` |
| Platform detection (8 platforms + custom/unknown, evidence + confidence) | `app/services/platforms.py` |
| Discovery sources: Common Crawl (raw index), crt.sh, own crawler, urlscan | `app/sources/`, `app/services/discovery.py` |
| Product extraction (JSON-LD / OpenGraph) + price/availability history | `app/services/products.py` |
| Monitoring: scheduler tick, activity classification, retries, recovery, retention | `app/services/monitoring.py` (beat service) |
| Intelligence signals (observed vs inferred, with evidence) | `app/services/signals.py` |
| Accounts, tenants, plans/limits, saved searches, alerts, CSV export, admin, opt-out | `app/services/{accounts,billing,workspace}.py`, `app/api/account.py` |
| Measured metrics, scaling notes, extra worker node | `app/services/metrics.py`, [docs/scaling.md](docs/scaling.md), `docker-compose.worker.yml` |

Not operational until configured: **Stripe billing** (`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
`STRIPE_PRICE_PRO`, `STRIPE_PRICE_BUSINESS`; endpoints return 501 until set) and **email alerts**
(alerts are in-app only; no SMTP integration yet). `URLSCAN_API_KEY` is optional.

## Operations

- Update a running server: `make deploy`.
- All monitoring intervals and per-tick budgets are env settings in `apps/api/app/config.py`.
- System metrics, users, plans and the opt-out list: Admin page (admins only).
