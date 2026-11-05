# M12-B report — discovery throughput (measured 2026-11-05, production, config-only changes)

All numbers measured from `crawl_jobs` / `stores` on production. Windows: baseline 06:00–11:00 UTC
(5 h), step 1 11:40–12:43 (1.05 h), step 2 12:50–13:46 (0.94 h; short windows → ±10 % noise).

| | Baseline | Step 1 | Step 2 (current) |
|---|---|---|---|
| Settings | in-flight 40, per-tick 60, 2 workers | in-flight 60 | in-flight 90, per-tick 90, 3 workers |
| 1–2. Verifications / h | 249 | 364 (+46 %) | **551 (+121 %)** |
| 3. New live stores / h | 160 | 237 | **378 (2.4×)** |
| 4. Worker utilisation | 53 % (of 2) | 77 % (of 2) | 84 % (of 3) |
| Verify avg / p95 | 1.92 s / 4.9 s | 1.79 s / 4.3 s | 2.01 s / 4.9 s |
| 5. Failed / retried / HTTP 429 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |

**4. Other resources (step 2):** host load avg 2.9–5.0 on 2 vCPU (incl. an unrelated Chrome process using
~0.7 core); RAM 4.6 GB available of 7.9 GB; worker container ~330 MB; Postgres ~185 MB, 5/100
connections, backlog query 3.4 ms; disk 75 GB free.

**6. Source contribution:** every new store in both step windows came from `common_crawl_shopify`
(237 / 353). The other sources had no new candidates left to verify at the front of the backlog.

**7. Actual cost attribution (7 d, each job counted once, split across sources sharing a domain):**
actual worker time = verification 48,461 s + products 62,178 s; unattributed (manual) 909 s; the
shares + unattributed sum exactly to the total.

| Source | New unique stores (7 d) | Attributed s | s / new unique store |
|---|---|---|---|
| common_crawl_shopify | 2,411 | 56,255 | 23 |
| urlscan (restricted, disabled) | 370 | 12,321 | 33 |
| common_crawl (.shop) | 96 | 37,359 | 389 |
| certificate_transparency | 1 | 3,713 | 3,713 |
| commerceradar_crawler | 1 | 83 | 83 |

Code: `metrics.processing_cost` (branch `m12b-cost-attribution`, replaces the old per-source sum
that counted shared domains once per source).

**8. Infrastructure limits:** (a) per-tick 90 × 6 ticks = 540 queued/h is the scheduling ceiling and
is now reached; (b) workers 84 % busy — more per-tick without more workers only builds a queue;
(c) the host CPU (2 vCPU, shared) is the hard limit. Product extraction (~21 s/job) uses
more worker time than verification — it is the largest cost per new store.

**9. Recommended next capacity:** hold at step 2. Pending backlog 22,560 ≈ 41 h at the current rate.
Next increases, in order: (1) move product extraction for new stores to off-peak / a separate
queue (frees ~55 % of worker time); (2) a second worker host via `docker-compose.worker.yml`
(paid — needs approval) or a ≥4 vCPU host, then per-tick 120 / 4 workers; measure each step.

## Rollout of the cost-attribution code (branch `m12b-cost-attribution`, 213 tests pass isolated)
No migration. Needs release approval. Steps: encrypted backup + checksum → `make deploy` →
health → `/admin/metrics` shows `processing_cost_7d` → tag `release/<date>-m12b`. Rollback =
redeploy the previous tag (no schema change). Runtime tuning stays in `apps/api/.env`
(delete the file to revert to code defaults).
