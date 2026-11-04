# CommerceRadar

Ecommerce intelligence + store discovery SaaS. Full master spec: `docs/spec.md` (read the relevant section before each milestone).
The core asset is the **database** (quality, coverage, freshness, history), not the dashboard.

## Status
- M1 Foundation: done.
- M2 Domain ingestion + verification: done (61 tests on Postgres; live-checked on VPS 2026-11-04). Known limit: no JS rendering — JS-only storefronts come out `uncertain`.
- M3 Platform detection: done (78 tests; live: allbirds.com → shopify 1.0). Rules in `app/services/platforms.py` (one rule list per platform; weak/ambiguous ⇒ unknown, confirmed store w/o fingerprint ⇒ custom 0.3). Only runs for confirmed/probable stores.
- Known limits: bot walls (403/429) ⇒ uncertain (not bypassed); stores whose homepage lacks cart/structured data (e.g. camelbak.com, SFCC) ⇒ uncertain; no JS rendering.
- M4 Discovery sources: done (103 tests; live 2026-11-04: common_crawl 8.6k hosts, urlscan 500, crt.sh 104, crawler 5 → 9k candidate domains, 0 duplicates). Adapters in `app/sources/` (`fetch_batch(config, cursor, state, ctx)`); one ingest path `app/services/discovery.py`; `discovery.run_batch` task chains one batch at a time (seq + expiring claim, resumable).
  - Common Crawl reads raw ZipNum index on data.commoncrawl.org via Range requests (the index.commoncrawl.org CDX server 504s). crt.sh only answers narrow queries (TLD-wide ⇒ 502) and takes 15-60s. urlscan keyless = 30 searches/min.
  - Fetcher tries IPv4 first: Docker network has no IPv6 route.
- M5 Product extraction: done (122 tests; live 2026-11-04: allbirds/aquila.com.au/activebaby.ca → 60 products, all with price+currency+availability, spot-checked vs live page). `app/services/products.py`: sitemaps (product sitemaps first) + homepage links → bounded pages/run (`PRODUCT_PAGES_PER_RUN`=20, 1s delay, robots Crawl-delay) → JSON-LD/OpenGraph → `products` + append-only `product_observations` (new/changed/removed/reappeared). Removal = 404/410 only (absence from a partial crawl proves nothing).
- Next: M6 continuous monitoring (scheduled re-verification + product re-checks, backlog sweep for ~9k unverified candidates, activity classification, failure recovery).

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
