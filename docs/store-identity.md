# Store identity (M10 phase 2)

One storefront can answer on many domains: a platform-hosted host (`x.myshopify.com`),
one or more custom domains (country/market domains), `www.` variants and old domains that
redirect. CommerceRadar keeps **every domain as its own record** and links domains to **one
store** with evidence. Code: `app/services/identity.py`; schema: migration `0012`.

## Data model

| Table / column | Meaning |
|---|---|
| `domains` | one row per normalized host (`www.` stripped, IDNA, lowercase) — unchanged |
| `stores.domain_id` | the store's **current primary** domain |
| `stores.storefront_key` | platform storefront id, e.g. `shopify:hello-archie.myshopify.com` (unique; only set when the response is served by the platform) |
| `stores.merged_into_store_id` | set on a duplicate store folded into another; its products and observations stay attached for history; hidden everywhere |
| `store_domains` | one row per domain↔store association: `role` (primary / alias / suggested), `alias_type`, `confidence`, `evidence` (final URL, redirect chain, serving IP, storefront key), `first_observed_at`, `last_observed_at`, `valid_from`, `valid_to` |

Constraints (partial unique indexes): a domain belongs to **at most one store at a time**
(`valid_to IS NULL AND role IN ('primary','alias')`); a store has **exactly one current
primary**. Associations are **closed, never deleted** — the full history of which domain
belonged to which store, and when, is preserved.

## Evidence ladder

| alias_type | Confidence | Evidence | May merge two existing stores? |
|---|---|---|---|
| `storefront_id` | 0.97 | Same Shopify shop id (`Shopify.shop`) **and** the response is served by Shopify: IP in 23.227.38.0/24 (Shopify's documented custom-domain address), Shopify cookies, or Shopify headers | yes |
| `platform_hosted` | 0.95 | `x.myshopify.com` / `x.mybigcommerce.com` redirects to the merchant domain | yes |
| `redirect_same_site` | 0.90 | `shop.brand.com` → `brand.com` (same registrable domain) | yes |
| `redirect` | 0.80 | `brand-a.com` → `brand-b.com` | **no** — links a new domain, never moves or merges an existing store |
| `canonical` | 0.60 | `<link rel=canonical>` / `og:url` on another host | no — `suggested` only |

Never used: store names, products, prices, design similarity. Redirects into marketplaces and
link hubs (Amazon, Etsy, eBay, Linktree, platform homepages…) are not identities.

### Why the HTML shop id alone is not enough (measured)
On production (2026-11-04) four gambling/spam domains (`kembarrrtogel.com`, `olxsurabaya.com`,
`subnhanhio.net`, `asecretgardenflst.net`) carried a real store's `Shopify.shop` id in copied
HTML. None was served by Shopify (no Shopify IP, cookies or headers). With the serving
requirement they stay separate; under an HTML-only rule they would have been merged into one
legitimate store.

## Primary domain

- A new storefront's primary is **where it actually lives** (final redirect host).
- A platform-hosted primary (`x.myshopify.com`) is **promoted** to a custom domain once that
  domain is linked at ≥ 0.9 (`primary_domain_change` observation; the old primary becomes an
  alias; the previous primary link is closed, not deleted).
- Future primary changes use the same mechanism.

## Merges

`identity.merge(loser, winner)` sets `loser.merged_into_store_id`, closes the loser's links and
re-opens them on the winner as `alias_type = merged`, and writes `identity_merge` /
`identity_merged_into` observations on both stores. Merges happen only at confidence ≥ 0.9.
The migration merges nothing: existing duplicates fold when a later verification supplies
strong evidence (first live result: `helloarchie.lu` and `helloarchie.de` folded into the
`helloarchie.fr` store; the four copied-HTML domains were not merged).

## Idempotency

Re-verifying the same domains refreshes `last_observed_at` only; no new links, one primary per
store (tested).

## Where identity is used

Verification (`apply_result`), product extraction target, domain detail (alias → store), store
detail (`domains` list with history), directory/counts/monitoring/signals/crawler (merged stores
excluded). Activity is driven by the **primary** domain only: a failing alias does not degrade
the store.

## Known limitations

- Storefront ids are implemented for Shopify only. BigCommerce store hashes, WooCommerce and
  others rely on redirects (follow-up).
- `registrable()` approximates eTLD+1 with a short list of ccSLDs (no public-suffix dependency).
- A store whose only evidence is a cross-site redirect keeps both domains separate unless a
  stronger signal appears; this is deliberate (false merges are worse than duplicates).
- Merged stores are hidden, not redirected, in the UI (`/stores/<merged id>` returns 404).
