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
- M6 Continuous monitoring: done (136 tests; live 2026-11-04). `app/services/monitoring.py`: activity rules (`update_activity`, failure kinds negative/transient/inconclusive), state-based `next_check`, `tick()` run by the `beat` compose service every MONITOR_TICK_SECONDS (bounded: rechecks/backlog/candidate rechecks per tick, scheduled sources via `schedule_hours`, stuck job/run recovery). Positive checks queue a product re-check (AUTO_EXTRACT_PRODUCTS). All intervals are env settings in `app/config.py`. Run exactly ONE beat.
- M7 Intelligence signals: done (151 tests; live 2026-11-04). `app/services/signals.py`: 11 signal types (growth/change/opportunity/health), basis observed|inferred, raw evidence, rows resolved never deleted. Refreshed after verification + product extraction + daily in the tick. Store directory/details/observations/platforms/signals API + pages.
  - LESSON: crt.sh `exclude=expired` makes every domain look new (90-day certs) — CT queries must read full history; ingest keeps the earliest first_discovered_at.
- M8 Commercial SaaS: done (166 tests; live 2026-11-04). Auth = `app/services/accounts.py` (scrypt, hashed bearer sessions, lockout) + `app/api/deps.py` (`current`, `admin`); ALL /api/v1 routes need a session, crawl/maintenance + /admin need admin. Tenant data: saved searches, alerts, exports, usage (`app/services/workspace.py`, `billing.py` PLANS). Stripe adapter returns 501 until STRIPE_* set. Suppression (`domains.suppressed_at`) blocks crawling everywhere. Admin user: `python -m app.cli create-admin EMAIL`. Web: cookie `cr_session` (httpOnly), `apiFetch`/`getJson` forward it; never put a generic path-taking function behind "use server".
- M9 Large-scale expansion: done (live 2026-11-04). `app/services/metrics.py` + `/admin/metrics` (measured throughput, p95, source yield, storage, queues); adaptive backlog (`MONITOR_MAX_IN_FLIGHT`); evidence retention (`compact_history`); EXPLAIN-justified indexes (0009); `docker-compose.worker.yml`; numbers + thresholds in `docs/scaling.md`. Deploy with `make deploy` (restarts web: Next dev watcher loses routes after branch switches).
- M10 + M11 (released to `main` 2026-11-04, `23c6077`, tag `release/2026-11-04-m10-m11`; recovery tag `recovery/pre-m10-main`): audit `docs/m10-audit-and-plan.md`; suppression hardening (`app/services/suppression.py`, fetcher guard on every hop); `domains.backlog_rank` (yield-ordered backlog); store identity (`app/services/identity.py`, `store_domains`, `stores.storefront_key`/`merged_into_store_id`, doc `docs/store-identity.md`); `common_crawl_shopify` source; `inactive_storefront` status; crt.sh evidence-only; benchmark harness (`app/services/benchmark.py`, `docs/benchmark.md`, CLI benchmark-*); `probe-shells`.
  - LESSON: an HTML `Shopify.shop` id is copied by spam sites — identity needs platform-served evidence (Shopify IP block / cookies / headers).
- M11: urlscan restricted+quarantined (`app/services/provenance.py`, `discovery_sources.restricted`); encrypted backups `infrastructure/scripts/backup.sh` + `restore-test.sh` (docs/backup-restore.md; NOT scheduled until an off-server destination exists); benchmark labels store/not_store/inactive_storefront/inaccessible/uncertain, holdout hidden unless final (logged); BigCommerce identity (x-bc-store-id + session cookie). Reports: docs/m11-report.md, docs/source-rights.md.
- M12 (plan docs/m12-plan.md): A unique-store contribution metrics (`metrics.unique_contribution`, `metric_snapshots` daily via tick) — branch `m12-unique-contribution`.
- Test a branch without touching production: `git worktree add /opt/cr-<name> origin/<branch>` then run pytest in a throwaway container from the api image with that tree mounted (see M12 notes in docs/m12-plan.md).
- Roadmap (spec §23) complete. Open: Stripe keys, SMTP for email alerts, public HTTPS exposure (Caddy), production Next build, repo still public.

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
