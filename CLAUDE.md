# CommerceRadar

Ecommerce intelligence + store discovery SaaS. Full master spec: `docs/spec.md` (read the relevant section before each milestone).
The core asset is the **database** (quality, coverage, freshness, history), not the dashboard.

## Status
- M1 Foundation: done (see README). Next: M2 domain ingestion + verification.

## Layout
- `apps/api/app` — FastAPI + SQLAlchemy + Celery in ONE package (modular monolith). API = `uvicorn app.main:app`, worker = `celery -A app.worker worker`. Same image, different command, so workers can move to other machines later.
- `apps/api/app/models.py` — all tables (spec §13). Changes ⇒ `alembic revision --autogenerate`, review, commit. `test_migrations_match_models` fails if models and migrations drift.
- `apps/web` — Next.js App Router. Server components call the API via `API_URL`; never the DB.

## Non-negotiable rules (spec §22)
- Candidate `domains` and verified `stores` stay separate; nothing enters `stores` without passing verification.
- Never fabricate data (revenue, traffic, sales, counts). Separate verified facts / estimates / inferred signals. Empty states over fake numbers.
- Keep source provenance; preserve history (record disappearance, don't delete).
- Jobs idempotent (`task_acks_late` is on — tasks can run twice).
- Route handlers thin; logic in `app/services`.
- SSRF protection on every outbound fetch (block private/loopback/link-local/metadata, revalidate after redirects). Respect robots.txt; never bypass auth/anti-bot.
- Secrets only in env vars. No paid services/infrastructure without explicit approval.
- Don't call an integration working until it's tested. Tests never hit random public websites — use fixtures/mocks.
- Status columns are strings validated in services (no native PG enums).

## Commands
- Local tests (SQLite, no services): `cd apps/api && .venv/Scripts/python -m pytest -q`
- Docker: `cp .env.example .env && make up`, tests vs Postgres: `make test`
