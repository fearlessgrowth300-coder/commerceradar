# Verification & platform accuracy benchmark (M10 phase 5)

**Status: harness built and tested; no labels collected yet; no accuracy figures exist.**
Do not quote precision/recall until `benchmark-eval --split holdout` has been run on a
properly sized holdout set.

## Ground truth (never produced by the classifier under test)

| label_source | How it is collected | What it measures |
|---|---|---|
| `vendor_showcase` | Domains copied by a person from platforms' own public showcase/example pages (e.g. Shopify, WooCommerce, BigCommerce, Wix, Squarespace, PrestaShop, OpenCart, Adobe Commerce customer pages). Label = ecommerce + that platform. | platform-detector accuracy; recall on known stores |
| `human_sample` | `python -m app.cli benchmark-sample --n 200` → stratified random sample of processed candidates per discovery source. The CSV is **blind** (no verdict column). A person opens each site and fills `is_ecommerce` (yes/no) and `platform`. | real-world precision/recall per source |
| `known_positive` / `known_negative` | Curated by a person with a note (e.g. news sites, government, universities, SaaS homepages as negatives). | regression guard |
| `hard_case` | Borderline sites found during review: JS-only storefronts, catalogue-only (no cart), marketplaces, link-in-bio shops, copied storefront HTML. | robustness |

Labelling rule: **ecommerce = a visitor can select a product and start a purchase on this
site** (a cart/checkout or equivalent). Catalogue-only, "contact us to buy" and marketplace
seller pages are labelled `no` with a note.

## Split (no leakage)

Every label is assigned once, by `sha256(domain) mod 100 < 30` → `holdout`, else `tune`.
The split can't change. Rule/threshold changes may only be evaluated on `tune`; reported
figures come from `holdout`.

## Metrics (`python -m app.cli benchmark-eval [--split holdout] [--live]`)

- Confusion over **decided** items (confirmed/probable = positive; not_ecommerce = negative).
- **Abstentions** (uncertain, unreachable, blocked, inactive storefront) reported separately;
  recall is given both over decided items and including abstentions.
- Precision, recall, false-positive rate, false-negative rate.
- Platform accuracy per platform (among labelled positives the system called positive).
- Breakdown by discovery source.
- Any rate with fewer than **30** items in its denominator is printed as `insufficient (n=…)`.
- `--live` re-verifies labelled domains now (read-only; nothing written to the database).

## Target sizes before reporting

| Set | Minimum |
|---|---|
| holdout positives | 100 (≥ 30 per platform you want a per-platform figure for) |
| holdout negatives | 100 |
| human_sample per discovery source | 50 |

## Workflow

```sh
docker compose exec api python -m app.cli benchmark-sample --n 200 > sample.csv   # blind
# a person labels sample.csv (is_ecommerce yes/no, platform, notes)
docker compose exec -T api python -m app.cli benchmark-import /tmp/sample.csv --label-source human_sample --labeller "<name>"
docker compose exec api python -m app.cli benchmark-eval --split tune      # while changing rules
docker compose exec api python -m app.cli benchmark-eval --split holdout   # what gets reported
```
