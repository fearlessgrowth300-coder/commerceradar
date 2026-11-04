# M11 — data trust, accuracy and expansion (report, updated 2026-11-04 22:15 UTC)

**Released 2026-11-04 22:44 UTC:** `main` fast-forwarded to the tested commit `23c6077` (tag `release/2026-11-04-m10-m11`), deployed with `make deploy`, migrations 0016. Previous `main` kept as tag `recovery/pre-m10-main` (`9346dbb`); branch `m10-discovery-quality` kept. 208 tests pass on Postgres; web type-check clean.

## 1. Shopify ingestion (measured)
- 17,028 `*.myshopify.com` hosts from Common Crawl CC-MAIN-2026-43.
- Representative sample: 300 hosts chosen by seeded hash across the whole range, prioritised; **all 300 processed by 22:15 UTC**:

  | Outcome | n | share |
  |---|---|---|
  | confirmed ecommerce | 194 | 64.7 % |
  | probable ecommerce | 7 | 2.3 % |
  | inactive storefront (password page / unavailable) | 40 | 13.3 % |
  | uncertain | 55 | 18.3 % — 45 scored 2, 5 scored 3, 3 robots.txt disallow, 2 HTTP 403 |
  | not ecommerce | 4 | 1.3 % |
  | unreachable / failed | 0 | 0 % |

  **Store rate 67.0 % (201/300; ~95 % interval ±5.3 pp).** 201 distinct stores, 0 duplicates; 199 live on the
  myshopify host itself, 2 redirected to a custom domain (linked as `platform_hosted` aliases).
  *Estimate, not a count:* applied to all 17,028 hosts this suggests roughly 10,500–12,300 stores once processed.
  The 45 "scored 2" hosts are the main open question for the benchmark (likely young Shopify stores with few
  visible products — not assumed either way). Temporary table `m11_shopify_sample` dropped.
- Overall at 22:15: 452/17,028 myshopify hosts processed, 16,576 remaining; 240 verifications/h, 0 failures.
- Identity (22:15): 756 live stores, 6 merged duplicates, 4 platform-hosted aliases, 22 canonical suggestions.

## 2. Accuracy benchmark
Labels: store / not_store / inactive_storefront / inaccessible / uncertain (last two never counted as yes/no); reviewer email + timestamp stored; split fixed by hash; holdout hidden unless `?final=true` and every holdout look logged. **No figures: no human labels yet.** Built: stratified blind queue of 197 domains (`benchmark_queue`, batch `m11-1`), admin page `/admin/benchmark` (no verdict shown), report `GET /api/v1/admin/benchmark/report` (cells n<30 marked insufficient). A person must label them.

## 3. Source rights
See `docs/source-rights.md`. urlscan **disabled and restricted** (ToS: commercial use and database compilation need written permission). Quarantine live: 370 stores found only via urlscan are hidden from customers, exports, alerts and customer counts; admins see them flagged; nothing deleted. Decision on that data still open. Common Crawl usable (legal advice recommended; we store index metadata only). crt.sh evidence-only.

## 4. BigCommerce identity
Measured 11/11 `*.mybigcommerce.com` storefronts send server-set `x-bc-store-id` + BigCommerce session cookie. Key `bigcommerce:<id>` requires both; HTML `s-<hash>` ignored (copyable). Tests cover alias, copied hash, header-without-cookie, idempotency.

## 5. Common Crawl expansion
`.shop`: 8,543 domains, 1,672 processed, 93 stores (5.6 %), 90 unique to CC, 3 overlapping; `.store`/`.boutique` not started (`.shop` ~3 % read). DB ≈ 2.1 KB/domain (52 MB total). Recommendation: **do not expand yet** — 16.8k myshopify + 6.9k CC candidates already ≈ 4 days of worker time at 250/h; expand `.store` after the myshopify yield is final.

## 6. Production merge readiness
- Done: deployed commit recorded; branch is 9 commits ahead of `main`, `main` has none missing (fast-forward); first verified backup taken (`/root/backups/commerceradar/`), restore tested in a separate container; downgrade-to-0009 tested on the copy: core rows preserved, but `store_domains`, `suppression_events`, `benchmark_labels` (and `benchmark_queue`) are lost and merged duplicates reappear → **forward release only**. 206 tests pass on Postgres; web type-check clean.
- Backups now: encrypted (AES-256 gpg), integrity-checked, never overwritten, 14-day local retention, OK/INCOMPLETE/FAILED log (`docs/backup-restore.md`). Encrypted backup + restore into a separate container verified at the release commit (817 stores, 13,190 products, rev 0016).
- Not done: **no off-server destination configured → not scheduled** (runs end INCOMPLETE). Needs owner: destination + copy the passphrase `/root/.commerceradar-backup.key` off the server. Then:
  `(crontab -l; echo "15 3 * * * /opt/commerceradar/infrastructure/scripts/backup.sh >> /var/log/commerceradar-backup.log 2>&1") | crontab -`
  and off-server copies of backups; merge into `main` (fast-forward) after owner approval.

## 7. Remaining
1. Human labelling of the 197-domain queue → first precision/recall.
2. Owner decisions: urlscan data, backup cron, merge approval.
3. `.store` expansion once the myshopify backlog (~16.6k, ≈ 3 days at 240/h) is mostly processed.
