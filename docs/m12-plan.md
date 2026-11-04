# M12 — technical plan (design only, not implemented)

Baseline (2026-11-04 22:45 UTC, `main` = `23c6077`, migrations 0016): 809 live stores (439 visible to
customers; 370 urlscan-only are quarantined), 26,214 candidate domains, ~240 verifications/h on one
2-process worker, 0 failed jobs. Measured yields: `myshopify.com` 67.0 % (n=300, ±5.3 pp),
Common Crawl `.shop` 5.6 % (93/1,672). Source rights: `docs/source-rights.md`.

## Guiding rules
- Expand acquisition only through sources whose terms are recorded in `docs/source-rights.md`.
- Count **unique live stores** (after alias consolidation, excluding restricted, suppressed and merged),
  never candidate domains, as the headline metric.
- Any classifier change ships only with a before/after on the **tune** split; holdout is looked at
  once per release (logged).

## Workstreams and tasks

### A. Unique-store contribution metrics (first; everything else is judged with it)
1. `metrics.unique_contribution()`: per source → live stores whose *every* linked domain was found by
   that source only; shared stores; restricted share. Uses `store_domains` (alias-aware), not raw domains.
2. Daily snapshot table `metric_snapshots` (date, metric, source, value) so trends are measured, not recomputed.
3. Admin metrics panel: unique vs shared contribution, stores per verification-hour per source.
Tests: fixture stores with overlapping/aliased domains; restricted exclusion.

### B. Complete Shopify-hosted ingestion
1. Let the 16,576 remaining `myshopify.com` candidates drain (≈ 3 days at 240/h; no code needed).
2. Re-measure on the full population; compare with the 67 % sample.
3. Inactive storefronts (13 %): schedule re-checks at 30 days (some open later) — reuse monitoring intervals.
4. When a newer CC index is published, set `common_crawl_shopify.index` → incremental (state is per index+suffix).
Capacity check: if the backlog > 48 h, add a second worker box (`docker-compose.worker.yml`, private network only).

### C. Common Crawl `.store` coverage
1. Precondition: B mostly drained (worker time is the constraint, not download).
2. New source row `common_crawl_store` (suffix `store`, ≈ 831 blocks, own state, rank 3) so `.shop`'s
   cursor and `.store`'s provenance stay separate.
3. Pilot: 30 blocks → measure candidates, unique domains, processed yield, overlap with existing
   sources, DB growth (≈ 2.1 KB/domain), worker hours per new store. Continue only if store
   yield per verification-hour beats `.shop`.

### D. BigCommerce identity (adapter built in M11)
1. Ingest `mybigcommerce.com` (≈ 16 index blocks) as its own CC source to exercise the adapter on
   real data; measure merges vs suggestions.
2. Add BigCommerce custom-domain detection via the same server signals (x-bc-store-id + session cookie).
3. Audit: list stores sharing an HTML `s-<hash>` without matching store-id headers (copied pages),
   like the Shopify clone audit.

### E. Multi-platform alias detection
1. Per platform, record which server-side identifiers exist before writing an adapter (evidence first):
   WooCommerce/Magento/PrestaShop/OpenCart have no hosted-platform id → only redirects + same-site rules.
   Wix (`x-wix-request-id` is per request, not per site) and Squarespace need investigation.
2. Cross-domain alias candidates from `hreflang` alternates (`<link rel=alternate hreflang>`) — recorded
   as `suggested` only; promoted to alias only with a platform storefront id match.
3. Admin review queue for `suggested` links (accept → alias with reviewer + timestamp; reject → kept as history).

### F. Benchmark-driven classification improvements
1. Collect the 197 human labels (reviewer task, `/admin/benchmark`).
2. Run `benchmark-eval --split tune`; analyse the largest error buckets — expected first candidates from
   production data: Shopify hosts that scored 2 (45 of 300 sample hosts) and JS-only pages.
3. Candidate rule changes (each behind a before/after on tune): e.g. Shopify-served + `Shopify.shop` +
   product sitemap ⇒ probable; never on HTML signals alone.
4. One holdout evaluation per release, recorded in `benchmark_evaluations`.
5. Second batch: vendor showcase domains (platform ground truth) entered by a person.

## Operational prerequisites (owner)
- Off-server backup destination + scheduled job (see `docs/backup-restore.md`); copy the encryption
  passphrase off the server.
- urlscan decision (permission / purge / keep quarantined).
- Legal review of Common Crawl usage before commercial launch.

## Order and rough effort
A (1–2 d) → B (wait + 0.5 d) → F1–F2 (labels: reviewer time) → C pilot (1 d + measurement) →
D (1 d) → E (2–3 d) → F3+ (per-rule, data-driven).

## VPS impact
A: negligible. B/C: worker-bound; +~2 KB per candidate (≈ 35 MB per 17k). D: one small CC source.
E: one extra HTML parse per verification (hreflang). No new services or paid dependencies.
