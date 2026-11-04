# M11 — data trust, accuracy and expansion (interim report, 2026-11-04 21:14 UTC)

Branch `m10-discovery-quality` (deployed on the VPS, commit `965683c`, migrations up to 0015). **Not merged into `main`.**

## 1. Shopify ingestion (measured)
- 17,028 `*.myshopify.com` hosts; 198 processed, 16,830 remaining (~250 verifications/h, 0 failures, avg 13 s).
- Representative sample: 300 hosts chosen by seeded hash across the whole range (`m11_shopify_sample`, temporary table — drop after use), rank 0.
  At 21:14: 66/300 processed → 48 confirmed, 3 probable, 6 inactive storefronts, 1 not ecommerce, 8 uncertain (7 scored 2, 1 HTTP 403), 0 unreachable.
  50 of 51 stores live on the myshopify host itself; 1 redirected to a custom domain (alias). **Final sample figures still pending** — re-run `docs/m11-measure` queries (scratch `m11_measure.sql`) once 300/300.
- Identity: 594 live stores, 6 merged duplicates, 2 platform-hosted aliases, 14 canonical suggestions.

## 2. Accuracy benchmark
**No figures: no human labels yet.** Built: stratified blind queue of 197 domains (`benchmark_queue`, batch `m11-1`), admin page `/admin/benchmark` (no verdict shown), report `GET /api/v1/admin/benchmark/report` (cells n<30 marked insufficient). A person must label them.

## 3. Source rights
See `docs/source-rights.md`. urlscan **disabled** (ToS: commercial use and database compilation need written permission); decision on 484 existing urlscan records open. Common Crawl usable (legal advice recommended; we store index metadata only). crt.sh evidence-only.

## 4. BigCommerce identity
Measured 11/11 `*.mybigcommerce.com` storefronts send server-set `x-bc-store-id` + BigCommerce session cookie. Key `bigcommerce:<id>` requires both; HTML `s-<hash>` ignored (copyable). Tests cover alias, copied hash, header-without-cookie, idempotency.

## 5. Common Crawl expansion
`.shop`: 8,543 domains, 1,672 processed, 93 stores (5.6 %), 90 unique to CC, 3 overlapping; `.store`/`.boutique` not started (`.shop` ~3 % read). DB ≈ 2.1 KB/domain (52 MB total). Recommendation: **do not expand yet** — 16.8k myshopify + 6.9k CC candidates already ≈ 4 days of worker time at 250/h; expand `.store` after the myshopify yield is final.

## 6. Production merge readiness
- Done: deployed commit recorded; branch is 9 commits ahead of `main`, `main` has none missing (fast-forward); first verified backup taken (`/root/backups/commerceradar/`), restore tested in a separate container; downgrade-to-0009 tested on the copy: core rows preserved, but `store_domains`, `suppression_events`, `benchmark_labels` (and `benchmark_queue`) are lost and merged duplicates reappear → **forward release only**. 206 tests pass on Postgres; web type-check clean.
- Not done: **daily backup cron not installed** (needs owner approval):
  `(crontab -l; echo "15 3 * * * /opt/commerceradar/infrastructure/scripts/backup.sh >> /var/log/commerceradar-backup.log 2>&1") | crontab -`
  and off-server copies of backups; merge into `main` (fast-forward) after owner approval.

## 7. Remaining
1. Finish the 300-host Shopify sample measurement; drop `m11_shopify_sample`.
2. Human labelling of the 197-domain queue → first precision/recall.
3. Owner decisions: urlscan data, backup cron, merge approval.
4. `.store` expansion after (1).
