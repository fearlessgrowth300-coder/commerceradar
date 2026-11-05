# M13 — Daily new-store pipeline (zone files → Shopify DNS filter → verify)

**Goal:** find Shopify stores within ~1 day of their domain being registered, instead of waiting
for Common Crawl (monthly) to see them. Status: built on branch `m13-zonefile`, source seeded
**disabled**. Needs ICANN CZDS approval + release approval before it runs.

## Flow (source `czds_zonefile`, adapter `app/sources/czds.py`)
1. Monitoring tick launches one run every `schedule_hours` (24).
2. Per TLD in `tlds`: authenticate (`CZDS_USERNAME`/`CZDS_PASSWORD` in `apps/api/.env`), list the
   account's approved zones, download `<tld>.zone` once per UTC day to the `zonedata` volume.
   Only links on `czds-api.icann.org` are followed.
3. `gzip | awk | sort -u | comm`: second-level names today minus yesterday = **new names**.
   Constant memory (disk sort). First day per TLD = baseline, nothing is "new".
   The raw zone and older lists are deleted at once; only today's name list is kept (for tomorrow's diff).
4. New names are resolved (apex A record, 32 threads, `dns_per_batch` names per batch). Only names
   inside `match_cidrs` (Shopify `23.227.38.0/24`) become candidates — everything else is dropped,
   never stored.
5. Candidates go through the normal `discovery.ingest`: suppression/opt-outs first, provenance
   (`source_ref = czds:<tld>:<day>:<ip>`), `backlog_rank` 1 (verified ahead of the rest), first 200
   queued at once, then normal verification. A DNS match is a hint, **not** a store.

## Expected volume (estimate, to be measured)
- New .shop/.store registrations: low thousands/day each → DNS takes minutes.
- Zone downloads: tens of MB/day for .shop/.store. `.com` (~5 GB/day download, ~150 M names) works with
  the same pipeline but needs disk + bandwidth review before enabling (`min_free_gb` guard stops the run).
- Yield (% of new names on Shopify IPs, % of those verifying as live stores) is unknown until measured;
  a fresh domain often has a "coming soon"/password page → `inactive_storefront`; monitoring re-checks.

## Not done (on purpose)
- `www`-only Shopify setups (CNAME `shops.myshopify.com`, apex elsewhere) — add a CNAME lookup if
  measured misses justify it.
- Other platforms' IP ranges — add CIDRs to `match_cidrs` once each is confirmed.
- App/technology detection — separate milestone.

## Rollout
1. Owner applies for CZDS access (below). 2. Credentials into `/opt/commerceradar/apps/api/.env`.
3. Release approval → deploy (migration 0018 only inserts the disabled source; worker gets the volume).
4. Enable, day 1 = baseline, day 2+ measure: new names, DNS matches, verified stores, worker seconds.
5. Report yield before adding TLDs.

## Owner: applying for CZDS access (free)
1. Create an ICANN Account at https://czds.icann.org (email + MFA), complete the CZDS user profile
   (name, organisation, address).
2. "Request access" → select `.shop`, `.store` (add `.online`, `.site`, `.com` later).
3. Reason: *"Research: identifying newly launched ecommerce websites for a market-intelligence
   database. Zone data is not redistributed or used for unsolicited contact."*
4. Accept the terms. Each registry approves separately (days, some up to ~30 business days).
   Access expires — renew from the request page (up to 30 days before expiry).
5. Send me nothing but "approved for X" — put the credentials on the VPS yourself, or tell me to.
