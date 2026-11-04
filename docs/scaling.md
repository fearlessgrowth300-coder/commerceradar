# Scaling CommerceRadar (Milestone 9)

Decisions here come from measurements on the production VPS (8 GB RAM, 2 vCPU shared with
other services, load average ~7–8), taken 2026-11-04 with `GET /api/v1/admin/metrics`
(also shown on the Admin page). Re-measure before acting on any threshold below.

## Measured baseline (first ~12 h of operation)

| Metric | Value |
|---|---|
| Candidate domains | 9.2k (Common Crawl 8.5k, urlscan 466, crt.sh 186, crawler 5) |
| Verification job | avg 19.7 s, p50 0.9 s (fast DNS/HTTP failures), p95 227 s (retries + timeouts) |
| Product extraction job (20 pages, 1 s delay) | avg 28.5 s, p95 38.7 s |
| Worker capacity (2 processes) | ≈ 365 verifications/h (`concurrency × 3600 / avg`) |
| Throughput before M9 | ~120/h — capped by the fixed 20-per-tick quota, **not** by the worker |
| Worker-seconds per new store | ≈ 290 s |
| Database | 18.5 MB, ≈ 2.1 KB per candidate domain (all tables) |
| Redis | < 6 MB |
| Store rate by source (verified candidates that became stores) | urlscan 64 %, crt.sh 10 %, Common Crawl 6 %, own crawler 0 % (5 samples) |

What this says:
- Verification is network-bound, not CPU-bound; the bottleneck was scheduling. M9 made the
  backlog budget adaptive (`MONITOR_MAX_IN_FLIGHT`, default 40): each tick tops the queue up
  instead of adding a fixed number.
- Source quality differs 10×. urlscan (`domain:myshopify.com`) is by far the best yield per
  verification; Common Crawl `.shop/.store/.boutique` is cheap to collect but mostly non-stores.
  Spend verification capacity accordingly (raise urlscan pages, keep CC as volume).

## Storage projection

At ≈ 2.1 KB/domain plus product history, one Postgres comfortably holds:
- 1 M domains ≈ 2 GB; 10 M ≈ 20 GB (fits this VPS's disk; RAM is the constraint for hot indexes).
- Growth drivers are `crawl_jobs.result` (evidence JSON, ~0.6 KB/check), `product_observations`
  and `store_observations`. Evidence of superseded checks older than
  `JOB_EVIDENCE_RETENTION_DAYS` (90) is compacted automatically to its summary.

**Partitioning is not justified yet.** Revisit when either is true (measure with the metrics
page / `pg_stat_user_tables`):
- `product_observations` or `store_observations` > ~50 M rows → monthly RANGE partitions on
  `observed_at` (queries are always per store + time window).
- `crawl_jobs` > ~20 M rows → partition by `scheduled_at` month and drop/archive old partitions.

## Indexes added from EXPLAIN on live data

| Query | Before | After |
|---|---|---|
| Backlog pick (pending, oldest first, no verify job) | 11 ms, hash anti-join over **all** pending rows | 0.24 ms, partial index `ix_domains_pending_first_seen`, stops after N |
| "In-flight job for this domain?" | `ix_crawl_jobs_domain_id` + filter | composite `ix_crawl_jobs_domain_type_status` |

Other hot queries (directory page, stale candidates, backlog count) were < 4 ms at this size.

## Adding worker machines

`docker-compose.worker.yml` runs only Celery workers against the main Postgres + Redis.

1. Put both servers on a private network (WireGuard or the provider's private LAN).
2. On the main server bind Postgres/Redis to the private address only, and set a Redis password.
   Never expose either publicly.
3. On the worker box: `.env` with `DATABASE_URL` / `REDIS_URL` → main server, then
   `docker compose -f docker-compose.worker.yml up -d --build`.
4. Keep exactly **one** `beat` (on the main server). Split queues per box with `WORKER_QUEUES`
   (e.g. a crawl box running `verification,products`).

When to add one: when `/admin/metrics` shows queues staying non-empty and measured/h close to
worker capacity for days, or when the main VPS's CPU steal/load (shared with other services)
slows the API. Each extra 2-process worker adds roughly 300–400 verifications/h at current
job durations. Postgres is not the bottleneck at these volumes.

## Not adopted (no measured need)

Kafka, Kubernetes, Elasticsearch, microservices, a separate analytics store. The modular
monolith + Postgres + Redis/Celery handles the measured load with large headroom; Postgres
full-text / trigram indexes are the next step for directory search before any search cluster.

## Cost per verified store

`/admin/metrics` reports worker-seconds per new store (measured) and, if
`VPS_MONTHLY_COST_USD` is set, a USD estimate (`monthly cost ÷ projected stores per month`).
The USD figure is an estimate and is labelled as such.

## Operating notes

- Update a running server: `make deploy` (pull, migrate, restart api/worker/beat/web). The web
  container runs the Next.js dev server, whose file watcher can lose routes after a branch
  switch — restarting it fixes that. A production `next build && next start` image would
  also cut the web container's RAM (~770 MB in dev mode).
- Queue depth, durations and source yield: Admin page → System metrics.
