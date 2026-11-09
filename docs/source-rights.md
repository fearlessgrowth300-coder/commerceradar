# Acquisition source rights (M11, reviewed 2026-11-04)

**Not legal advice.** This records what each provider's own published terms say, quoted where
possible, and the operational decision taken. A lawyer should confirm before commercial launch.

## Summary

| Source | Status after review | Why |
|---|---|---|
| urlscan.io | **Disabled** (`enabled=false`, no schedule) | Terms prohibit commercial use and database compilation without express written permission |
| Common Crawl | Active (discovery) | Terms allow use incl. commercial, with a strong recommendation to get legal advice; third-party content rights stay with site owners |
| crt.sh / Certificate Transparency | Active as **evidence only** | No published terms found; CT logs are public by design; operator rate limits apply |
| Own crawler (stores' public pages) | Active | robots.txt honoured, opt-out list, no bot-wall bypass; content rights belong to site owners |
| ICANN CZDS zone files (`czds_zonefile`) | **Built, disabled** — pending CZDS approval + legal review | Registry terms allow lawful use; forbid mass unsolicited advertising and high-volume automated queries to registry/registrar systems. Raw zone deleted after diff; the latest full list of domain names stays on the VPS disk for the next diff (not in the database, never redistributed); only DNS-matched names enter the database |
| Manual submissions | Active | — |

## urlscan.io — terms of service (https://urlscan.io/terms/, read 2026-11-04)

| Topic | Terms (quoted) |
|---|---|
| Commercial use | "The Site may not be used in connection with any commercial endeavors" · "Commercial use of any part of our service requires express written permission." |
| Database compilation | prohibited to "Systematically retrieve data or other content from the Site to a compile database or directory" "without written permission from us" |
| Automated access | prohibited to "Use any robot, spider, scraper or other automated means to access the Service" (API use is governed by the API docs, rate limits and quotas) |
| Redistribution | prohibited to "resell or distribute in any way material, information or functionalities from the Service"; "no part of the Site, Services or Our Content may be copied, reproduced, aggregated, republished … without our express prior written permission" |
| Storage / retention | not specified |
| Derived signals in a commercial product | not addressed directly; covered by the commercial-use and compilation clauses above |
| Commercial path | "For our commercial customers we offer separate terms of service." About page: commercial API plans via sales@urlscan.io |

**Assessment.** CommerceRadar is a commercial SaaS and the urlscan source systematically
retrieved search results to compile a store database — both need written permission we do
not have. Ingestion is stopped. **Open decision for the owner:** what to do with the 484
existing urlscan-sourced candidate records (389 stores share at least one urlscan record;
every store was independently verified by our own crawler, but discovery came from urlscan).
Options: (a) obtain written permission / a commercial plan (paid — needs approval), (b) purge
urlscan-only provenance and any store found *only* via urlscan, (c) keep pending legal advice.
No action taken on existing data.

## Common Crawl — terms of use (https://commoncrawl.org/terms-of-use, last updated 7 March 2024)

- Use is under a limited licence; uses outside the ToU are not authorised (§1).
- Commercial use is not prohibited; Common Crawl "strongly recommends that you obtain the advice
  of legal counsel before making any use, including commercial use" (§3).
- **Crawled content belongs to its owners:** it "may be subject to separate terms of use or terms
  of service from the owners of such Crawled Content" (§2); users must respect third-party
  copyrights (§3) and must not invade privacy (§2(b)).
- No attribution requirement and no stated rate limits; abusive use can lead to termination (§3).

**What we take from Common Crawl:** only index metadata — URL, hostname, capture timestamp,
HTTP status, MIME type (`source_ref` = index id). We do **not** download or store WARC page
content. Store facts (ecommerce status, platform, products) come from our own robots-respecting
fetch of the live site, not from Common Crawl content. Rights in that live content stay with the
site owners: we store extracted facts and short evidence snippets, not copies of pages, and honour
opt-outs.

## crt.sh / Certificate Transparency

- No terms of service published by crt.sh (Sectigo) were found; the operator has announced
  per-IP rate limiting (60 req/min, 2020). CT logs are public by design (RFC 6962).
- Since M10 crt.sh is evidence-only: certificate first-seen dates for domains we already know;
  shown as `first_certificate_observed_at` and never as a launch date.

## Rules going forward

1. A new source is added only after its terms are recorded here, with commercial use, database
   compilation, storage, redistribution and attribution each answered.
2. Paid plans or licences need explicit owner approval.
3. Provenance (`discovered_urls.source_id` + `source_ref`) is kept so any source's contribution
   can be purged if its rights change.

## ICANN CZDS zone files (reviewed 2026-11-05)

- Access per TLD is granted by each registry through ICANN CZDS; it expires and must be renewed.
- Registry zone-file policies (e.g. the ICANN-published zone file access agreement and registry CZDS
  policies) allow use "only for lawful purposes" but never to support mass unsolicited commercial
  advertising/solicitation, nor to enable high-volume automated processes that query registry or
  registrar systems. Some registries add their own terms — read each one when approved.
- **Our use:** the raw zone is deleted after the diff, but the latest full list of second-level names stays on the VPS disk for the next day's diff (check each registry's retention terms). Only names that resolve to a commerce
  platform's IP range enter the database as candidates (with provenance), then are verified from the
  public website. No zone data is exported or shown to customers. DNS lookups go to our resolver,
  not to registry/registrar systems.
- **Open risk:** CommerceRadar customers may use store lists for outreach. Customer terms should
  forbid spam; a lawyer should confirm this use before enabling.
