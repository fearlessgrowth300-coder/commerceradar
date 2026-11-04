# M10 — Discovery expansion & data quality: audit and plan

Measured on the production database at **2026-11-04 17:01 UTC** (queries in
[`docs/m10-audit-queries.sql`](m10-audit-queries.sql)). No figure below is estimated unless
marked *estimate*. The "42 stores / 64 % / 10 % / 6 %" figures in the M9 report were a
snapshot from ~12:20 UTC; the system kept running, so they are superseded here.

## 1. Metric definitions

| Metric | Definition (SQL source) |
|---|---|
| Candidate record | one `discovered_urls` row = one URL from one source (same URL from two sources = 2 records) |
| Unique normalized domain | one `domains` row (`normalized_domain` is unique; `www.` stripped, IDNA, lowercase) |
| Processed | domain whose `verification_status <> 'pending'` (≥ 1 completed verification) |
| Live website | latest check `current_resolution_status = 'resolved'` (DNS resolved, public IP, HTTP response) |
| Confirmed / probable | `domains.verification_status` from the latest verification |
| Verified store | a `stores` row (only created by a confirmed/probable verification) |
| Active / inactive / uncertain store | `stores.activity_status` (M6 rules) |
| Recheck | a verify job for a domain that already had an earlier verify job |
| Source yield | stores ÷ **processed** candidates of that source (not ÷ all candidates) |
| Unique contribution | stores whose domain was found **only** by that source |

## 2. Verified baseline

| Metric | Value |
|---|---|
| Candidate records (all sources) | 9,262 |
| Unique normalized domains | 9,189 |
| Suppressed (opt-out) domains | 0 |
| Processed at least once | 1,486 (16.2 %) |
| **Never processed (backlog)** | **7,703 (83.8 %)** — oldest candidate seen 2026-10-07 |
| Live websites (resolved) | 1,046 of 1,486 processed (70.4 %) |
| Confirmed ecommerce | 70 |
| Probable ecommerce | 17 |
| Verified stores (rows) | 87 = 87 active, 0 inactive, 0 uncertain, 0 recently active |
| Products recorded | 1,151 (all active) across 72 stores |
| New verified stores per day | all 87 first verified on 2026-11-04 (system went live that day) |
| Rechecks | 19 jobs, 19 succeeded (100 %), 18 still a store, 0 failed |
| Verification throughput | 236–244 per hour (13:00–16:00 UTC), avg 17–26 s per job |
| Failed jobs | 0 (2 retrying, 1 running at measurement time) |

Store platforms: Shopify 53, WooCommerce 14, unknown 10 (all probable), custom 6, Wix 2,
Squarespace 1, Magento 1.

## 3. Acquisition source performance

| Source | Records | Unique domains | Dup records in source | Processed | Confirmed | Probable | Active stores | Yield (stores ÷ processed) | Unique-to-source stores | Shared with other sources | Unreachable (DNS/net) | Runs / failures |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Common Crawl (`.shop/.store/.boutique`) | 8,587 | 8,543 | 44 | 1,283 | 52 | 17 | 69 | **5.4 %** | **66** | 3 | 315 (24.6 %) | 1 / 0 |
| urlscan (`domain:myshopify.com`) | 484 | 466 | 18 | **25** | 16 | 0 | 16 | **64 %** | 3 | 13 | 1 | 1 / 0 |
| Certificate Transparency (crt.sh) | 186 | 186 | 0 | 184 | 18 | 0 | 18 | 9.8 % | **2** | 16 | **124 (67 %)** | 2 / 0 (many crt.sh 502 retries) |
| Own crawler | 5 | 5 | 0 | 5 | 0 | 0 | 0 | 0 % | 0 | 0 | 0 | 1 / 0 |
| Manual submissions | 5 | 5 | 0 | 5 | 0 | 0 | 0 | 0 % | 0 | 0 | 0 | — |

Provider constraints (measured / documented):

| Source | Constraint |
|---|---|
| Common Crawl | No API quota; raw index read via HTTP Range on `data.commoncrawl.org`. The CDX query server 504s (measured). Index blocks per suffix (CC-MAIN-2026-43): `.shop` ≈ 2,113, `.store` ≈ 831, `.boutique` ≈ 16, `myshopify.com` ≈ 95, `wixsite.com` ≈ 214, `mybigcommerce.com` ≈ 16. **Read so far: 60 blocks of `.shop` (~3 %).** Terms: community statements say commercial use isn't excluded but republishing/reselling raw crawl data is not allowed — we store only hostnames/URLs we then verify ourselves. Confirm against the current Terms of Use. |
| urlscan | Unauthenticated, per IP: **30 searches/minute** (measured `x-rate-limit-*` headers); hourly/daily windows also exist (not shown without a key). Only HTTP 200 counts toward quota. Commercial terms not confirmed — check before scaling. |
| crt.sh | Operator announced 60 requests/IP/minute (2020); in practice it returns 502s and hangs under load (measured repeatedly). No SLA, no API docs. |

### Findings that change the M9 numbers

1. **The "64 % urlscan yield" is real but tiny**: 16 of 25 processed. 441 urlscan candidates
   (the highest-yield pool we have) are still unprocessed because the backlog is worked
   oldest-first and Common Crawl filled the queue first.
2. **The "10 % crt.sh yield" is mostly circular**: crt.sh is seeded with `%.<known store>`, so it
   re-discovers stores we already have (16 of 18 shared). Unique contribution: **2 stores**.
   67 % of its candidates are subdomains that do not resolve.
3. **Common Crawl contributes 66 of the 71 unique stores (93 %)** despite the lowest yield.
   Low yield × huge, cheap volume = most of our coverage.

## 4. Bottlenecks — why 87 stores from 9,189 candidates

Outcome of the **1,486 processed** domains (latest check):

| Outcome | Count | Share of processed | Cause |
|---|---|---|---|
| Store (confirmed + probable) | 87 | 5.9 % | — |
| Not ecommerce, scored 0 | 484 | 32.6 % | Parked pages, gambling/spam `.shop` names, info sites (sampled titles: "Parking Page", slot sites, link directories) |
| Not ecommerce, scored 1 | 26 | 1.7 % | One weak signal (prices only) |
| Uncertain — empty / JS-only page | **323** | **21.7 %** | Page needs JavaScript; we don't run JS. **Largest classification gap.** |
| Uncertain — HTTP 403 | 72 | 4.8 % | Bot walls / forbidden — respected, not bypassed |
| Uncertain — other 4xx / 429 / robots | 25 | 1.7 % | Correctly not crawled |
| Uncertain — scored 2–3 | 29 | 2.0 % | Borderline: e.g. cart link only (sample includes real shops `abbeycrafts.shop`, `230v.shop`, `oldstars.2k5.shop`) |
| DNS: no such domain | 337 | 22.7 % | Dead CC hosts and crt.sh subdomains |
| Network error after retries | 99 | 6.7 % | Timeouts / refused |
| Non-public IP | 4 | 0.3 % | SSRF block (correct) |

**Primary bottleneck — processing backlog, not detection.** 83.8 % of candidates have never
been checked. At the measured ~240/h the current backlog clears in ≈ 32 h (*estimate* from
measured rate), but new sources will refill it; ordering matters more than speed.

**Secondary — candidate quality.** For Common Crawl `.shop`, 37 % of processed candidates are
dead or parked/spam. Ordering the backlog by expected yield (source + cheap host heuristics)
puts verification capacity where stores are.

**Tertiary — JS-only storefronts (323) and borderline scores (29)** may hide real stores. No
evidence yet of how many; must be measured with a labelled sample before changing thresholds.

Not bottlenecks (measured): job failures (0), queue delay for already-queued jobs (minutes),
SSRF blocks (4), robots disallows (7), source duplication (3 % of CC stores shared).

## 5. Monitoring efficiency (Task 6)

- The **10-minute tick is the scheduler cadence, not the recheck frequency.** Each store has its
  own `next_check_at`: all 87 stores are next due 2026-11-10 → 11-12 (7-day "new store" interval);
  **0 stores due within 24 h**.
- Per tick (measured): up to 10 store rechecks, adaptive backlog (≤ 60, queue capped at 40 in
  flight), 5 stale candidate rechecks; observed ≈ 40 verifications per tick.
- Repeated checks: 18 domains had ≥ 2 verify jobs in 24 h — the one-off M6 migration recheck
  (stores with `next_check_at = NULL`) plus manual resubmissions. Not ongoing waste.
- Changes detected so far: 0 product changes (no store has been re-extracted after its first
  extraction yet), 70 activity changes (all initial "uncertain → active").
- Already adaptive by state (new 7 d, active 21 d, changed 2 d, transient failure 1 d, bot wall
  14 d, inactive 60 d). Proposed refinement (§8 step 7): stretch the interval for stores with
  repeated unchanged checks, shrink it for stores whose catalogue changed.

## 6. Data integrity & suppression (Task 7) — gaps found in code review

| Stage | Today | Gap |
|---|---|---|
| Source ingestion | `ingest()` does **not** check suppression | Suppressed domain's URLs are still recorded; a **new subdomain** of a suppressed domain (e.g. `shop.optedout.com`) becomes a new candidate and is verified |
| Queue submission | manual submit + monitoring tick check exact domain | Exact host only — no parent-domain match |
| Website crawling | verify/products/crawler check the job's own domain | **Redirects are not checked**: a non-suppressed domain redirecting to a suppressed one gets fetched; product extraction uses `store.canonical_url`, which may be another host |
| Scheduled rechecks | excluded (exact host) | parent-domain gap |
| Product extraction | task checks the job's domain | canonical-host gap (above) |
| Ordinary views | directory + store detail hide suppressed | **Visible in**: `/domains` list + detail, `/signals`, dashboard "recent changes", platform counts, `/products/{id}` |
| Audit trail | `suppressed_at` + reason on the domain | **Un-suppressing erases the record**; no who/when history |
| Aliases | none | `x.myshopify.com` → custom domain redirects would create two candidates for one store (relevant before adding the myshopify source) |

## 7. Security & compliance concerns

1. Suppression gaps above (highest priority — an opt-out must hold across sources, subdomains, redirects).
2. Third-party terms must be confirmed before scaling: Common Crawl ToU (no republishing raw
   data), urlscan commercial use, crt.sh fair use. **Rejected for now:** OpenStreetMap
   (ODbL share-alike could attach to our derived database — needs legal review), Tranco
   (combined list includes Cloudflare Radar data under CC BY-NC — non-commercial).
3. JS rendering (headless browser) would raise CPU/RAM sharply on the shared VPS and increases
   bot-wall friction; prefer evidence that needs no JS (sitemaps, platform fingerprints).

## 8. Recommended changes — ordered, incremental

| # | Change | Why (evidence) | Size | Tests |
|---|---|---|---|---|
| 1 | **Suppression hardening**: parent-domain matching; guard in `SafeFetcher` (blocks suppressed hosts on every hop incl. redirects); skip at ingest; hide in all views; `suppression_events` audit log | §6 gaps | S | unit: subdomain/redirect/ingest/view exclusion; regression on existing flows |
| 2 | **Yield-ordered backlog**: pending candidates ordered by source priority (urlscan > CC > crt.sh) then age; config per source | urlscan 64 % vs CC 5.4 % yield; 441 urlscan pending | S | tick ordering test; measure stores/hour before vs after |
| 3 | **Domain aliases** (`domain_aliases`: alias host → canonical domain, recorded from verification redirects); dedupe stores by canonical host | prerequisite for #4; prevents duplicate stores | M | alias recording, dedupe, suppression via alias |
| 4 | **Common Crawl platform suffixes**: add `myshopify.com` (≈95 blocks), `mybigcommerce.com` (≈16) as CC targets; continue `.shop`/`.store` | ~100 % platform-hosted stores; cheap to read | S (config + #3) | adapter tests exist; measure yield per suffix |
| 5 | **crt.sh repurposed**: stop store-seeded discovery queries (circular); keep CT for launch-date evidence; weekly | 2 unique stores, 67 % dead candidates | S | adapter test |
| 6 | **Verification evaluation harness**: labelled dataset (`label, platform, label_source`) built from vendor showcase pages (independent ground truth) + human-labelled random sample via an admin labelling page; CLI reports precision/recall per class/platform | Required before any threshold change | M | the harness itself; numbers recorded in docs |
| 7 | **No-JS second look for empty shells** (only after #6 gives a baseline): sitemap product URLs + platform fingerprints → `probable` | 323 shells (21.7 %) | S–M | fixtures + precision/recall delta from #6 |
| 8 | **Monitoring refinement**: stretch interval after N unchanged checks, cap per-tick rechecks by queue depth; report "checks per change detected" | §5 | S | interval tests |
| 9 | Optional sources to evaluate later, terms first: vendor showcase lists (small, high precision), Majestic Million (CC BY 3.0, low ecommerce density) | — | — | — |

Each step: run tests, deploy, record before/after in this file.

## 9. Expected impact on the 8 GB VPS

- #1–#3, #5, #8: negligible (queries + one small table).
- #4: `myshopify.com` ≈ 95 blocks × ~250 KB ≈ 24 MB downloaded once; maybe tens of thousands of
  new candidates → DB +≈ 2 KB each (≈ 100 MB at 50k) — well within limits (DB today 18.5 MB).
  Verification time is the real cost: at 240/h, 50k candidates ≈ 9 days of worker time, which is
  why #2 (yield ordering) comes first.
- #6: one-off labelling fetches; no ongoing load.
- #7: one extra request (sitemap) for ~22 % of checks → ≈ +5–10 % verification time (*estimate*).
- No new services, no paid dependencies.

## 10. Progress log (before → after, measured)

### Step 1 — suppression hardening (done, `c9e4b58`)
| Check | Before | After |
|---|---|---|
| New subdomain of an opted-out domain from any source | became a candidate and was verified | dropped at ingest (`stats.suppressed`) |
| Redirect from an allowed domain into an opted-out one | followed and fetched | refused inside `SafeFetcher` on every hop → `blocked` |
| Manual submit of `sub.optedout.com` | accepted | 422 |
| Existing subdomain candidates when a parent opts out | unaffected | marked covered; queued jobs failed; reversed exactly on un-suppress |
| Views exposing suppressed data | `/domains`, `/signals`, recent changes, platform counts, products, observations | none (admin suppression list only) |
| Audit trail | erased on un-suppress | `suppression_events` (who/when/why), backfilled |
| Tests | 173 | 180 (7 new, all passing on Postgres) |
Production impact: 0 opt-outs exist today, so no rows changed.

### Step 2 — yield-ordered backlog (done, `cc0a544`)
- Pending candidates by rank after backfill: urlscan (rank 1) 441, Common Crawl (rank 3) 7,217, crt.sh (rank 5) 2.
- Backlog query with the new partial index `(backlog_rank, first_seen_at)`: 0.24 ms (unchanged from M9).
- **Before** (13:00–16:00 UTC, oldest-first): ≈ 240 verifications/h, 52 new stores in 4 h ≈ **13 stores/h**.
- **After**: _measured below once the urlscan pool has been worked (~2 h)._
- Tests: 181.
