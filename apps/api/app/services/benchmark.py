"""Labelled accuracy benchmark (M10 phase 5).

Ground truth never comes from the classifier being measured:
  vendor_showcase  domains listed on a platform vendor's public showcase (positive + platform)
  human_sample     stratified random candidates labelled by a person, BLIND (the sample CSV
                   does not show our verdict)
  known_positive / known_negative / hard_case   curated by a person, with a note

Split: every label is assigned once, by hash of the domain, to `tune` (≈70 %) or `holdout`
(≈30 %). Thresholds/rules may be tuned only against `tune`; reported numbers come from
`holdout`. The split cannot change later, so holdout examples cannot leak into tuning.

Figures are only reported with their sample sizes; cells with n < MIN_N are marked
"insufficient" instead of a percentage.
"""

import csv
import hashlib
import io
import random
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BenchmarkLabel, DiscoveredUrl, DiscoverySource, Domain, Store
from app.services.domains import InvalidDomain, normalize_domain

LABEL_SOURCES = ("vendor_showcase", "human_sample", "known_positive", "known_negative", "hard_case")
PLATFORMS = ("shopify", "woocommerce", "wix", "bigcommerce", "magento", "prestashop", "squarespace", "opencart", "custom")
POSITIVE = ("confirmed_ecommerce", "probable_ecommerce")
ABSTAIN = ("uncertain", "temporarily_unreachable", "blocked", "pending", "inactive_storefront")
MIN_N = 30
HOLDOUT_SHARE = 30  # percent


def split_for(domain: str) -> str:
    return "holdout" if int(hashlib.sha256(domain.encode()).hexdigest(), 16) % 100 < HOLDOUT_SHARE else "tune"


# ---------- sampling (blind) ----------


def sample_csv(db: Session, n: int, seed: int) -> str:
    """Stratified by discovery source over PROCESSED candidates. No verdict columns (blind)."""
    rows = db.execute(
        select(Domain.normalized_domain, DiscoverySource.name)
        .join(DiscoveredUrl, DiscoveredUrl.domain_id == Domain.id)
        .join(DiscoverySource, DiscoverySource.id == DiscoveredUrl.source_id)
        .where(Domain.verification_status != "pending", Domain.suppressed_at.is_(None))
    ).all()
    by_source: dict[str, list[str]] = defaultdict(list)
    for dom, src in rows:
        by_source[src].append(dom)
    rng = random.Random(seed)
    already = set(db.scalars(select(BenchmarkLabel.domain)))
    per = max(1, n // max(1, len(by_source)))
    picked: list[tuple[str, str]] = []
    for src, doms in sorted(by_source.items()):
        pool = sorted(set(doms) - already)
        picked += [(d, src) for d in rng.sample(pool, min(per, len(pool)))]
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["domain", "url", "found_by", "is_ecommerce", "platform", "notes"])
    for d, src in picked:
        w.writerow([d, f"https://{d}/", src, "", "", ""])
    return out.getvalue()


# ---------- import ----------


def import_csv(db: Session, text: str, label_source: str, labeller: str) -> dict[str, int]:
    if label_source not in LABEL_SOURCES:
        raise ValueError(f"label_source must be one of {LABEL_SOURCES}")
    added = updated = skipped = 0
    for row in csv.DictReader(io.StringIO(text)):
        raw = (row.get("is_ecommerce") or "").strip().lower()
        if raw not in ("yes", "no", "y", "n", "true", "false", "1", "0"):
            skipped += 1  # unlabelled rows are ignored, never guessed
            continue
        try:
            dom = normalize_domain(row.get("domain") or "")
        except InvalidDomain:
            skipped += 1
            continue
        is_ecom = raw in ("yes", "y", "true", "1")
        platform = (row.get("platform") or "").strip().lower() or None
        if platform and platform not in PLATFORMS:
            raise ValueError(f"{dom}: unknown platform {platform!r}")
        existing = db.scalar(select(BenchmarkLabel).where(BenchmarkLabel.domain == dom))
        if existing:
            existing.is_ecommerce, existing.platform = is_ecom, platform if is_ecom else None
            existing.label_source, existing.labeller, existing.notes = label_source, labeller, (row.get("notes") or "")[:500]
            updated += 1
        else:
            db.add(BenchmarkLabel(domain=dom, is_ecommerce=is_ecom, platform=platform if is_ecom else None,
                                  label_source=label_source, labeller=labeller, split=split_for(dom),
                                  notes=(row.get("notes") or "")[:500]))  # fmt: skip
            added += 1
    db.commit()
    return {"added": added, "updated": updated, "skipped_unlabelled": skipped}


# ---------- evaluation ----------


def _rate(num: int, den: int) -> float | str:
    return round(num / den, 3) if den >= MIN_N else f"insufficient (n={den})"


def evaluate(db: Session, split: str = "holdout", predictions: dict[str, tuple[str, str | None]] | None = None) -> dict:
    """Compare labels with the system's latest verdicts (or `predictions` from a fresh live run:
    domain -> (status, platform))."""
    labels = list(db.scalars(select(BenchmarkLabel).where(BenchmarkLabel.split == split)))
    if predictions is None:
        predictions = {}
        q = select(Domain.normalized_domain, Domain.verification_status, Store.primary_platform).outerjoin(
            Store, Store.domain_id == Domain.id).where(Domain.normalized_domain.in_([lb.domain for lb in labels]))  # fmt: skip
        for dom, status, platform in db.execute(q):
            predictions[dom] = (status, platform)
    sources = dict(db.execute(
        select(Domain.normalized_domain, DiscoverySource.name).join(DiscoveredUrl, DiscoveredUrl.domain_id == Domain.id)
        .join(DiscoverySource, DiscoverySource.id == DiscoveredUrl.source_id)
        .where(Domain.normalized_domain.in_([lb.domain for lb in labels]))
    ).tuples().all())  # fmt: skip

    tp = fp = tn = fn = abstain_pos = abstain_neg = 0
    per_source: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    plat_total: dict[str, int] = defaultdict(int)
    plat_right: dict[str, int] = defaultdict(int)
    for lb in labels:
        status, platform = predictions.get(lb.domain, ("pending", None))
        src = sources.get(lb.domain, "not in database")
        if status in ABSTAIN:
            abstain_pos += lb.is_ecommerce
            abstain_neg += not lb.is_ecommerce
            per_source[src]["abstained"] += 1
            continue
        pred = status in POSITIVE
        if pred and lb.is_ecommerce:
            tp += 1
            if lb.platform:
                plat_total[lb.platform] += 1
                plat_right[lb.platform] += platform == lb.platform
        elif pred:
            fp += 1
        elif lb.is_ecommerce:
            fn += 1
        else:
            tn += 1
        per_source[src]["tp" if pred and lb.is_ecommerce else "fp" if pred else "fn" if lb.is_ecommerce else "tn"] += 1

    decided = tp + fp + tn + fn
    return {
        "split": split,
        "labels": len(labels),
        "decided": decided,
        "abstained": {"labelled_positive": abstain_pos, "labelled_negative": abstain_neg},
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "precision": _rate(tp, tp + fp),
        "recall_decided": _rate(tp, tp + fn),
        "recall_including_abstentions": _rate(tp, tp + fn + abstain_pos),
        "false_positive_rate": _rate(fp, fp + tn),
        "false_negative_rate": _rate(fn, fn + tp),
        "platform_accuracy": {p: _rate(plat_right[p], n) for p, n in sorted(plat_total.items())},
        "by_source": {s: dict(v) for s, v in sorted(per_source.items())},
        "min_n_for_a_figure": MIN_N,
    }
