# COMMERCE RADAR — MASTER PROJECT BUILD SPECIFICATION

## 1. YOUR ROLE

You are the lead software architect, senior full-stack engineer, data engineer, web discovery engineer, security engineer and technical product builder responsible for developing CommerceRadar.

You are not simply creating a website dashboard. You are building a scalable ecommerce intelligence and store discovery platform.

You must think like an engineer building a serious commercial SaaS product that will eventually process millions of ecommerce domains and continuously maintain an accurate database of active online stores.

You must prioritize:
- Reliable data acquisition.
- Accurate ecommerce store verification.
- Multi-platform coverage.
- Continuous discovery.
- Historical data.
- Efficient infrastructure.
- Data quality.
- Security.
- Maintainable architecture.
- Scalable background processing.

Do not build unnecessary features just to make the application look impressive. Build working functionality.

---

# 2. PROJECT IDENTITY

Project name: CommerceRadar

Product category: Ecommerce Intelligence and Store Discovery SaaS.

Core mission:

Build a continuously updated global database of ecommerce stores across multiple ecommerce platforms.

CommerceRadar should discover, verify, classify, monitor and analyze online stores.

It should eventually help users answer questions such as:

- How many ecommerce stores exist?
- Which new ecommerce stores have appeared?
- Which stores are actively selling products?
- Which platforms are stores using?
- Which stores have recently added products?
- Which stores have removed products?
- Which stores appear to be expanding their catalogues?
- Which stores have recently changed their websites?
- Which stores have observable signs of inactivity?
- Which stores may have business opportunities or need ecommerce services?
- How has a store changed over time?

CommerceRadar must become an independent data asset rather than relying entirely on purchased third-party databases.

## 3. CORE PRODUCT OBJECTIVES

CommerceRadar has two primary functions.

### A. Ecommerce Store Discovery

Continuously discover ecommerce websites from multiple permitted sources.

The system must support:

- Shopify
- WooCommerce
- Wix
- BigCommerce
- Magento / Adobe Commerce
- PrestaShop
- Squarespace Commerce
- OpenCart
- Custom ecommerce platforms
- Other detectable ecommerce technologies

The platform must not be Shopify-only.

### B. Ecommerce Intelligence

After discovering and verifying a store, collect and maintain useful public information.

Potential information includes:

- Store domain.
- Store name.
- Ecommerce platform.
- Platform detection confidence.
- Publicly visible product catalogue.
- Product titles.
- Product URLs.
- Public prices.
- Product availability when observable.
- Currency when detectable.
- Product categories.
- Public contact information.
- Store status.
- First discovery date.
- Last successful verification.
- Last observed change.
- Source of discovery.
- Historical observations.
- Technology signals.

Do not fabricate revenue, sales, traffic, order counts or business performance.

Clearly distinguish verified facts, estimates and inferred signals.

---

# 4. INFRASTRUCTURE AND DEVELOPMENT ENVIRONMENT

Our current infrastructure choices:

Hosting: Hostinger VPS.

VPS RAM: 8 GB.

Operating system: Ubuntu 24.04 LTS, subject to checking the actual server.

Domain: Already owned. Actual domain will be supplied separately.

Repository: New private GitHub repository.

Development environment: Claude Code.

Development team: Human developer + AI + developers when needed.

Budget: Lean.

Architecture: Modular monolith with independent background workers.

The application must be designed for an initial 8 GB VPS.

Avoid unnecessary services, expensive infrastructure and premature microservices.

Make it possible to move background workers to separate servers later.

## 5. REQUIRED TECHNOLOGY STACK

### Frontend
- Next.js
- TypeScript
- React
- Tailwind CSS
- A consistent reusable component system.

### Backend
- Python
- FastAPI
- Pydantic
- SQLAlchemy
- Alembic migrations.

### Database
- PostgreSQL.

### Background processing
- Redis.
- Celery workers.
- Celery Beat or an equivalent scheduler.

### Website inspection
- HTTPX for normal HTTP requests.
- BeautifulSoup or selectolax for HTML parsing.
- Playwright only when JavaScript rendering is genuinely required.

### Deployment
- Docker.
- Docker Compose.
- Nginx reverse proxy.
- HTTPS.
- Environment-based configuration.

### Source control
- Private GitHub repository.

Use compatible, maintained and tested dependency versions. Pin dependencies appropriately.

Do not introduce additional technologies without explaining why they are necessary.

---

# 6. SYSTEM ARCHITECTURE

Use a modular monolith.

Keep the application logically separated into the following areas:

1. Frontend dashboard.
2. Backend API.
3. Store discovery services.
4. Domain verification services.
5. Platform detection services.
6. Product extraction services.
7. Background workers.
8. Monitoring services.
9. Data intelligence services.
10. Security and validation services.
11. External source integrations.

The frontend must not communicate directly with the database.

All application data should flow through the backend API.

Long-running operations must be processed asynchronously.

The API should submit jobs to the background queue and return job identifiers and statuses.

Workers must be independently scalable.

---

# 7. DATA ACQUISITION STRATEGY

This is one of the most important parts of CommerceRadar.

We have chosen a mostly-free acquisition strategy.

Do not make the product dependent on paid store databases.

Use multiple permitted discovery sources.

## Source 1: Common Crawl

Official website: https://commoncrawl.org/
Index: https://index.commoncrawl.org/

Purpose:
- Discover URLs from large public web crawls.
- Find candidate ecommerce websites.
- Identify product and category URLs.
- Discover previously unknown domains.
- Revisit selected crawl indexes for new candidates.

Requirements:
- Build a Common Crawl source adapter.
- Support configurable crawl indexes.
- Store source provenance.
- Support incremental processing.
- Avoid repeatedly processing identical URLs.
- Respect service rate limits.
- Process index results efficiently.
- Do not download unnecessary crawl data.

Do not assume Common Crawl represents the current state of every website.

## Source 2: Certificate Transparency

Potential public source: https://crt.sh/

Purpose:
- Discover newly observed domain names and subdomains.
- Add candidate domains to the discovery pipeline.
- Identify possible new businesses.

Requirements:
- Build a Certificate Transparency adapter.
- Deduplicate discovered hostnames.
- Normalize domain names.
- Record observation dates when available.
- Apply rate limits.
- Verify candidates before treating them as stores.

Certificate records do not prove that a domain is an ecommerce business.

## Source 3: CommerceRadar's Own Crawler

This is a strategic component.

Our crawler should discover additional public websites through permitted links and sitemaps.

Possible discovery paths:
- Public sitemaps.
- Public product pages.
- Public category pages.
- Public internal links.
- Public external links where appropriate.
- Previously verified store websites.

Requirements:
- Crawl only permitted public pages.
- Respect robots.txt and site-specific restrictions.
- Use conservative request rates.
- Support configurable crawl depth.
- Avoid crawl traps and infinite URL patterns.
- Store discovered links for later processing.
- Prevent duplicate crawling.
- Maintain source provenance.

Do not crawl behind authentication or bypass anti-bot protections.

## Source 4: Internet Archive

Website: https://web.archive.org/

Purpose:
- Historical website investigation.
- Identify previously observed ecommerce sites.
- Compare historical snapshots where permitted.
- Support historical analysis.

This is supplementary. Historical existence must never be treated as proof of current activity.

## Source 5: urlscan.io

Website: https://urlscan.io/

Purpose:
- Supplement domain discovery.
- Investigate public website scan records.
- Obtain useful website observations where API access and terms permit.

Requirements:
- Check current API limits and usage terms.
- Use it as an optional source.
- Do not make it a critical dependency.
- Avoid exceeding free quotas.

## Source 6: Future optional providers

Keep the architecture ready for optional adapters such as:
- BuiltWith.
- Wappalyzer.
- Store Leads.
- Domain intelligence feeds.
- Other licensed data providers.

Do not purchase subscriptions or integrate paid providers without explicit approval.

## Data source architecture

Create a shared source interface.

Every source adapter should support appropriate operations such as:

- Fetch candidate URLs.
- Fetch candidate domains.
- Record source metadata.
- Record source-specific identifiers.
- Track collection progress.
- Handle errors.
- Resume interrupted collection runs.

A source should never directly insert unverified websites into the verified stores table.

All candidates must pass through the verification pipeline.

---

# 8. CANDIDATE DOMAIN AND VERIFIED STORE SEPARATION

This is a critical architectural decision.

CommerceRadar must maintain two different concepts.

### Candidate domain

A domain discovered from any source that has not yet passed ecommerce verification.

Examples:
- Ordinary business websites.
- Blogs.
- Parked domains.
- Newly registered domains.
- Unknown websites.
- Potential ecommerce stores.

### Verified ecommerce store

A domain that has passed our ecommerce verification requirements.

Never mix ordinary candidate domains with verified stores.

A candidate may later become a verified store when new evidence is discovered.

A verified store may later become inactive without deleting its historical records.

Maintain the distinction throughout the database and frontend.

---

# 9. WEBSITE VERIFICATION ENGINE

Build a verification engine that evaluates whether a website is an actual ecommerce store.

The engine should inspect:

1. DNS resolution.
2. HTTP response.
3. Redirect destination.
4. Page title and metadata.
5. Product page patterns.
6. Product structured data.
7. Cart functionality indicators.
8. Checkout functionality indicators.
9. Ecommerce platform indicators.
10. Public product catalogue signals.

Use a confidence-based classification system.

Possible classifications:

- Confirmed ecommerce.
- Probable ecommerce.
- Not ecommerce.
- Uncertain.
- Temporarily unreachable.

A website should not be classified as ecommerce merely because it contains words such as "shop", "product" or "cart".

Store the evidence supporting every important classification.

Do not delete uncertain candidates.

---

# 10. PLATFORM DETECTION ENGINE

Create independent platform detection adapters.

Initial platforms:

1. Shopify.
2. WooCommerce.
3. Wix.
4. BigCommerce.
5. Magento / Adobe Commerce.
6. PrestaShop.
7. Squarespace.
8. OpenCart.
9. Custom.
10. Unknown.

Each detector must return:

- Detected platform.
- Confidence score.
- Evidence collected.
- Detection method.
- Detection timestamp.

Use deterministic detection rules first.

Do not make paid AI calls for every website.

AI may later assist with ambiguous cases, but it must not replace evidence-based detection.

A platform must be marked unknown when evidence is insufficient.

---

# 11. STORE ACTIVITY CLASSIFICATION

We want all currently active ecommerce stores.

Create an activity classification system.

Possible statuses:

- Active.
- Recently active.
- Inactive.
- Uncertain.

Use evidence such as:
- Website availability.
- Recent successful verification.
- Product page availability.
- Public product catalogue changes.
- Cart or purchasing functionality.
- Redirects and domain expiration signals where available.

Do not assume that a store has closed because it fails one request.

Use multiple checks and failure-specific retry rules.

Do not claim a store is making sales merely because its website is active.

Store:
- Current status.
- Status evidence.
- Status confidence.
- Last successful check.
- Last failed check.
- Next scheduled check.

---

# 12. PRODUCT EXTRACTION ENGINE

For verified stores, extract publicly accessible product information where permitted.

Potential fields:

- Product title.
- Product URL.
- Product description.
- Product image URL.
- Product price.
- Currency.
- Availability.
- Product category.
- SKU when publicly available.
- Last observed timestamp.
- Source URL.

Support structured product data such as JSON-LD when available.

Do not invent missing information.

Preserve historical product observations.

If a product disappears, record the change rather than immediately deleting its historical record.

Avoid downloading unnecessary images.

---

# 13. DATABASE DESIGN

Use PostgreSQL and SQLAlchemy.

Create proper database models, foreign keys, indexes and migrations.

Initial tables should include:

### discovery_sources
Stores the source definitions.
Fields: id, name, source_type, configuration, enabled, created_at, updated_at

### discovery_runs
Tracks each collection operation.
Fields: id, source_id, status, started_at, completed_at, records_found, records_processed, records_failed, error_summary

### discovered_urls
Stores discovered URL records.
Fields: id, normalized_url, hostname, source_id, discovery_run_id, first_discovered_at, last_discovered_at, processing_status

### domains
Stores normalized domain identities.
Fields: id, normalized_domain, first_seen_at, last_seen_at, current_resolution_status, created_at, updated_at

### stores
Stores verified ecommerce stores.
Fields: id, domain_id, store_name, canonical_url, ecommerce_status, activity_status, primary_platform, platform_confidence, first_verified_at, last_verified_at, last_observed_at, created_at, updated_at

### platform_detections
Stores detection evidence.
Fields: id, store_id, platform, confidence, evidence, detection_method, detected_at

### products
Stores current product records.
Fields: id, store_id, external_product_id, product_url, title, description, current_price, currency, availability, image_url, first_seen_at, last_seen_at, created_at, updated_at

### store_observations
Stores historical observations.
Fields: id, store_id, observation_type, observation_data, observed_at, source_id

### crawl_jobs
Tracks asynchronous processing.
Fields: id, target_domain, job_type, status, priority, attempts, scheduled_at, started_at, completed_at, error_message

### crawl_policies
Stores site-specific crawl controls and restrictions.

Include appropriate policy status, robots.txt observation, allowed paths, disallowed paths, request limits and timestamps.

### contact_records
For future use, store publicly available business contact information with provenance, collection time and suppression status.

Use UUID identifiers where appropriate.

Create unique constraints to prevent duplicate normalized domains.

Use database indexes for:
- normalized domain.
- platform.
- activity status.
- discovery date.
- verification date.
- observation date.
- processing status.

Do not create giant unindexed tables.

Use pagination for large datasets.

Do not load millions of rows into application memory.

Design for future partitioning and archival when necessary, but do not introduce complex partitioning prematurely.

---

# 14. BACKGROUND WORKER ARCHITECTURE

Use Celery and Redis.

Create separate task categories:

- source_collection_tasks
- domain_normalization_tasks
- website_verification_tasks
- platform_detection_tasks
- product_extraction_tasks
- store_monitoring_tasks
- historical_observation_tasks

Workers must support:

- Retries.
- Exponential backoff where appropriate.
- Job timeouts.
- Failure logging.
- Idempotent processing.
- Duplicate prevention.
- Queue priorities.
- Scheduled tasks.
- Graceful shutdown.

One failed website must not crash an entire discovery run.

Use configurable concurrency appropriate for an 8 GB VPS.

Do not launch thousands of simultaneous browser instances.

Use HTTP requests before browser automation.

---

# 15. CONTINUOUS DISCOVERY AND MONITORING

CommerceRadar must not become a static database.

Build recurring jobs to:

1. Collect new candidate domains.
2. Process unverified candidates.
3. Recheck active stores.
4. Recheck uncertain stores.
5. Detect platform changes.
6. Detect product additions.
7. Detect product removals.
8. Detect price changes.
9. Detect availability changes.
10. Update store observations.

Initial suggested monitoring intervals:

- New candidate: initial verification.
- Newly verified store: approximately every 7 days.
- Established active store: approximately every 14–30 days.
- Recently changed store: approximately every 1–3 days when appropriate.
- Inactive store: approximately every 30–90 days.

Make all intervals configurable.

Respect crawl policies and adjust frequency to site response and change history.

---

# 16. SECURITY AND RESPONSIBLE CRAWLING

Security is mandatory.

Implement SSRF protection.

Block:
- localhost.
- Loopback addresses.
- Private IPv4 ranges.
- Private IPv6 ranges.
- Link-local addresses.
- Cloud metadata endpoints.
- Internal network destinations.

Revalidate resolved IP addresses after redirects and DNS changes.

Use:
- Connection timeouts.
- Read timeouts.
- Response-size limits.
- Redirect limits.
- Content-type validation.
- URL validation.
- Crawl-depth limits.
- Rate limits.
- Per-domain request limits.

Never execute downloaded website scripts.

Treat website content as untrusted input.

Do not bypass login systems, paywalls, anti-bot protections or access controls.

Respect robots.txt, applicable laws, provider terms and website-specific restrictions.

Use public business contact details only where permitted.

Maintain opt-out and suppression mechanisms.

Never put API keys, passwords or credentials in source code.

Keep secrets in environment variables.

Protect API endpoints and administrative operations.

---

# 17. FRONTEND DASHBOARD

Build a professional SaaS dashboard.

Design direction: clean, modern, professional, data-focused, responsive, easy to navigate, fast.

Avoid excessive gradients, decorative graphics and fake analytics.

Use actual database records only.

Initial navigation:

### Dashboard
Display: total candidate domains, total verified stores, active stores, inactive stores, new stores discovered, platform distribution, recent discovery runs, recent store changes, failed jobs.

### Store Discovery
Allow users to: submit a domain manually, start a permitted discovery run, view processing status, view discovery sources, review candidate domains, see verification results.

### Store Directory
Allow users to: search stores, filter by platform, filter by activity status, filter by discovery date, filter by country when reliable data is available, sort results, paginate results.

### Store Details
Display: store name, domain, platform, activity status, detection evidence, products, historical observations, first discovered date, last verified date, source history.

### Discovery History
Display: source runs, processing counts, successful records, failed records, job status, processing duration.

### Settings
Include configuration interfaces where appropriate, with sensitive settings protected.

Do not display invented store counts, fake graphs or fake revenue statistics.

Use empty states when no data exists.

---

# 18. API DESIGN

Use FastAPI with versioned routes.

Initial routes:

- GET /health
- GET /api/v1/dashboard/overview
- POST /api/v1/discovery/domains
- POST /api/v1/discovery/runs
- GET /api/v1/discovery/runs
- GET /api/v1/discovery/runs/{run_id}
- GET /api/v1/domains
- GET /api/v1/domains/{domain_id}
- GET /api/v1/stores
- GET /api/v1/stores/{store_id}
- GET /api/v1/stores/{store_id}/products
- GET /api/v1/stores/{store_id}/observations
- GET /api/v1/platforms

Use proper validation and response schemas.

Add pagination, filtering, sorting and consistent error handling.

Do not put business logic inside route handlers.

Keep API routes thin and move application logic into services.

---

# 19. REPOSITORY STRUCTURE

Build a clean repository. Use this as the architectural direction, not a requirement to create empty folders.

```
commerceradar/
  CLAUDE.md, README.md, .env.example, .gitignore, docker-compose.yml, Makefile
  apps/
    web/ (app/, components/, features/, lib/, package.json)
    api/
      app/ (main.py, config.py, api/, models/, schemas/, repositories/, services/, security/)
      migrations/, tests/, pyproject.toml
  workers/ (discovery/, crawling/, detection/, extraction/, monitoring/)
  integrations/
    sources/ (base.py, adapters/)
    platforms/ (base.py, shopify.py, woocommerce.py, wix.py, bigcommerce.py, magento.py, prestashop.py, squarespace.py, opencart.py)
  infrastructure/ (nginx/, docker/, scripts/, backups/)
  docs/ (architecture.md, database.md, api.md, security.md, discovery-sources.md, deployment.md)
```

Create files and folders only when they serve an actual implementation.

---

# 20. PERFORMANCE AND SCALABILITY

The initial server has 8 GB RAM. Optimize for that environment.

Requirements:

- Use asynchronous HTTP requests where appropriate.
- Keep browser automation limited.
- Use bounded worker concurrency.
- Use database connection pooling.
- Use pagination.
- Use efficient bulk inserts.
- Avoid unnecessary database queries.
- Add indexes for common filters.
- Avoid storing unnecessary raw HTML.
- Store only useful evidence and metadata.
- Keep background jobs small and recoverable.
- Use structured logs.
- Track processing metrics.

Design the system so that additional workers can later be deployed on separate machines.

The long-term goal is millions of store records, but do not claim that the current VPS can process unlimited crawling.

Do not prematurely introduce Kafka, Kubernetes, Elasticsearch or microservices. Only propose them when measurable requirements justify the additional infrastructure.

---

# 21. TESTING REQUIREMENTS

Every significant feature needs tests.

Include: unit tests, API integration tests, database tests, domain normalization tests, SSRF protection tests, redirect validation tests, duplicate prevention tests, platform detection tests, ecommerce verification tests, worker retry tests, source adapter tests, monitoring tests.

Use controlled test fixtures and mock external services.

Do not make the automated test suite depend on random public websites.

Create a small, documented collection of synthetic and permitted test fixtures.

Do not claim a feature is complete unless its relevant tests pass.

---

# 22. DEVELOPMENT RULES

1. Inspect the repository before modifying it.
2. Read this CLAUDE.md file before starting work.
3. Understand existing code before creating replacements.
4. Do not overwrite working code unnecessarily.
5. Do not generate huge amounts of unrelated code.
6. Work in approved milestones.
7. Build functional vertical slices.
8. Keep business logic out of API handlers.
9. Use migrations for database changes.
10. Add tests for new functionality.
11. Update documentation.
12. Use environment variables for configuration.
13. Never expose secrets.
14. Never fabricate data.
15. Never claim that external integration works without testing it.
16. Keep source provenance.
17. Preserve historical observations.
18. Make jobs idempotent.
19. Use typed interfaces.
20. Explain architectural decisions.
21. Report unfinished work honestly.
22. Ask for approval before adding paid services or infrastructure.

When you encounter a missing credential, subscription, API key or external permission, build the adapter interface and document what is required. Do not pretend the integration is operational.

---

# 23. DEVELOPMENT ROADMAP

Follow these milestones in order.

## MILESTONE 1 — FOUNDATION
Build: repository foundation, Docker Compose environment, Next.js dashboard starter, FastAPI application, PostgreSQL, Redis, Celery worker, health checks, environment configuration, database migrations, initial models, basic API communication, automated tests.

Acceptance: Docker Compose starts successfully. Frontend loads. Backend health endpoint works. PostgreSQL is healthy. Redis is healthy. Worker connects to Redis. Database migrations run. Automated tests pass.

## MILESTONE 2 — DOMAIN INGESTION AND VERIFICATION
Build: domain normalization, candidate domain table, manual domain submission, duplicate detection, SSRF protection, website verification, job tracking, basic dashboard display.

Acceptance: A user submits a domain, the system validates it, creates a candidate record, queues a verification job, processes it and displays the actual result.

## MILESTONE 3 — MULTI-PLATFORM DETECTION
Build: Shopify, WooCommerce, Wix, BigCommerce, Magento, PrestaShop, Squarespace and OpenCart detectors; custom/unknown classification.

Acceptance: The system detects supported platforms using evidence and records confidence.

## MILESTONE 4 — DISCOVERY SOURCE INTEGRATIONS
Build: Common Crawl adapter, Certificate Transparency adapter, CommerceRadar crawler, optional urlscan adapter, source-run tracking, incremental discovery, deduplication.

Acceptance: Multiple sources can add candidate domains to the same processing pipeline without duplicating records.

## MILESTONE 5 — PRODUCT EXTRACTION
Build: public product extraction, JSON-LD parsing, product persistence, price observations, product change tracking.

Acceptance: A verified store can have its publicly observable product information saved and updated.

## MILESTONE 6 — CONTINUOUS MONITORING
Build: scheduled verification, activity classification, product monitoring, change detection, historical observations, failure recovery.

Acceptance: Previously verified stores can be checked again and their records updated without losing historical data.

## MILESTONE 7 — INTELLIGENCE AND OPPORTUNITY SIGNALS
Build evidence-based signals such as: recently launched store, rapid catalogue expansion, product catalogue changes, missing visible ecommerce functionality, technology changes, broken or outdated website indicators, other observable business opportunities.

Clearly distinguish observations from assumptions. Do not claim a business is struggling, profitable or making sales without reliable evidence.

## MILESTONE 8 — COMMERCIAL SAAS
Only after the discovery system works reliably, build: user accounts, authentication, multi-tenancy, subscription plans, usage limits, saved searches, alerts, exports, billing, admin dashboard.

## MILESTONE 9 — LARGE-SCALE EXPANSION
Evaluate: additional worker machines, database optimization, partitioning, data archival, more discovery providers, large-scale monitoring, cost per verified store, processing throughput.

Scale based on actual performance measurements.

---

# 24. FIRST TASK

Begin with Milestone 1. Do not start production deployment. Do not assume that the VPS has already been configured. Do not assume the domain DNS is configured. Do not invent credentials. Use local development defaults through `.env.example`.

After each implementation, report: files created, files modified, architecture implemented, commands to start the application, local URLs, test results, known limitations, next recommended task.

## FINAL PRODUCT STANDARD

CommerceRadar must become a reliable, evidence-based ecommerce intelligence platform.

The most important asset is not the dashboard. It is the quality, coverage, freshness and historical value of our ecommerce database.

Every architectural and engineering decision should help us build that asset sustainably.
