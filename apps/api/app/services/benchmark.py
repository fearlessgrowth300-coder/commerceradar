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

from app.models import BenchmarkLabel, BenchmarkQueue, CrawlJob, DiscoveredUrl, DiscoverySource, Domain, Store
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
            existing.label = "store" if is_ecom else "not_store"
            existing.label_source, existing.labeller, existing.notes = label_source, labeller, (row.get("notes") or "")[:500]
            updated += 1
        else:
            db.add(BenchmarkLabel(domain=dom, is_ecommerce=is_ecom, label="store" if is_ecom else "not_store",
                                  platform=platform if is_ecom else None,
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
    human_abstained = sum(lb.is_ecommerce is None for lb in labels)
    inactive_agree = sum(1 for lb in labels if lb.label == "inactive_storefront"
                         and predictions.get(lb.domain, ("", None))[0] == "inactive_storefront")  # fmt: skip
    inactive_total = sum(lb.label == "inactive_storefront" for lb in labels)
    labels = [lb for lb in labels if lb.is_ecommerce is not None]
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
        "human_could_not_judge": human_abstained,
        "inactive_storefront_agreement": _rate(inactive_agree, inactive_total),
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



# ---------- stratified blind labelling queue (M11) ----------

# stratum → target count in a 200-domain batch. Strata come from OUR verdict at sampling time,
# so every kind of case is represented; the labeller never sees the stratum.
STRATA = {
    "confirmed_shopify": 30, "confirmed_woocommerce": 15, "confirmed_other_platform": 15, "probable": 15,
    "not_ecommerce": 35, "uncertain_js_shell": 25, "uncertain_bot_blocked": 20, "unreachable": 15,
    "inactive_storefront": 15, "uncertain_borderline_score": 15,
}  # fmt: skip


def _stratum(status: str, platform: str | None, reason: str, points) -> str | None:
    if status == "confirmed_ecommerce":
        return {"shopify": "confirmed_shopify", "woocommerce": "confirmed_woocommerce"}.get(platform or "", "confirmed_other_platform")
    if status == "probable_ecommerce":
        return "probable"
    if status == "not_ecommerce":
        return "not_ecommerce"
    if status == "inactive_storefront":
        return "inactive_storefront"
    if status in ("temporarily_unreachable", "blocked"):
        return "unreachable"
    if status == "uncertain":
        if "no server-rendered content" in reason:
            return "uncertain_js_shell"
        if "HTTP 403" in reason or "HTTP 429" in reason:
            return "uncertain_bot_blocked"
        if points in (2, 3, "2", "3"):
            return "uncertain_borderline_score"
    return None


def build_queue(db: Session, batch: str, seed: int, targets: dict[str, int] | None = None) -> dict[str, int]:
    """Fill the blind labelling queue. Idempotent per domain (unique)."""
    targets = targets or STRATA
    latest = (
        select(CrawlJob.domain_id, CrawlJob.result, CrawlJob.completed_at)
        .where(CrawlJob.job_type == "verify", CrawlJob.status == "succeeded")
        .subquery()
    )
    rows = db.execute(
        select(Domain.normalized_domain, Domain.verification_status, Store.primary_platform, latest.c.result, latest.c.completed_at)
        .join(latest, latest.c.domain_id == Domain.id)
        .outerjoin(Store, Store.domain_id == Domain.id)
        .where(Domain.verification_status != "pending", Domain.suppressed_at.is_(None))
    ).all()  # fmt: skip
    newest: dict[str, tuple] = {}
    for dom, status, platform, result, done in rows:  # keep each domain's latest check
        if dom not in newest or (done and newest[dom][3] and done > newest[dom][3]):
            newest[dom] = (status, platform, result or {}, done)
    pools: dict[str, list[str]] = defaultdict(list)
    for dom, (status, platform, result, _) in newest.items():
        ev = result.get("evidence") or {}
        s = _stratum(status, platform, ev.get("reason") or "", (ev.get("score") or {}).get("points"))
        if s in targets:
            pools[s].append(dom)
    taken = set(db.scalars(select(BenchmarkQueue.domain))) | set(db.scalars(select(BenchmarkLabel.domain)))
    rng = random.Random(seed)
    added: dict[str, int] = {}
    for s, n in targets.items():
        pool = sorted(set(pools.get(s, [])) - taken)
        pick = rng.sample(pool, min(n, len(pool)))
        db.add_all(BenchmarkQueue(domain=d, stratum=s, batch=batch, status="open") for d in pick)
        added[s] = len(pick)
    db.commit()
    return added


def next_item(db: Session) -> BenchmarkQueue | None:
    # random order inside the open queue so strata are interleaved (the labeller can't infer them)
    open_ = list(db.scalars(select(BenchmarkQueue).where(BenchmarkQueue.status == "open")))
    if not open_:
        return None
    return sorted(open_, key=lambda q: hashlib.sha256(q.domain.encode()).hexdigest())[0]


VERDICTS = {  # human label -> is_ecommerce (None = the human could not judge; excluded from precision/recall)
    "store": True,
    "not_store": False,
    "inactive_storefront": False,  # storefront exists but is closed / password-protected
    "inaccessible": None,  # did not load, blocked, or asked for a login
    "uncertain": None,  # loaded, but the reviewer cannot tell
}


def record(db: Session, domain: str, verdict: str, platform: str | None, notes: str, labeller: str) -> None:
    """One human judgement. Re-labelling the same domain keeps one row (latest answer, new timestamp)."""
    from datetime import UTC, datetime

    item = db.scalar(select(BenchmarkQueue).where(BenchmarkQueue.domain == domain))
    if item is None:
        raise ValueError("domain is not in the labelling queue")
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    if platform and platform not in PLATFORMS:
        raise ValueError(f"unknown platform {platform!r}")
    now = datetime.now(UTC)
    item.status, item.done_at = "labelled", now
    lb = db.scalar(select(BenchmarkLabel).where(BenchmarkLabel.domain == domain))
    if lb is None:
        lb = BenchmarkLabel(domain=domain, split=split_for(domain))  # split fixed by hash, never chosen
        db.add(lb)
    lb.label, lb.is_ecommerce = verdict, VERDICTS[verdict]
    lb.platform = platform if verdict in ("store", "inactive_storefront") else None
    lb.label_source, lb.labeller, lb.notes, lb.labelled_at = "human_sample", labeller, (notes or "")[:500], now
    db.commit()


def progress(db: Session) -> dict:
    from sqlalchemy import func

    by = db.execute(select(BenchmarkQueue.status, func.count()).group_by(BenchmarkQueue.status)).tuples().all()
    return dict(by)


def report(db: Session, final: bool = False, requested_by: str | None = None) -> dict:
    """TUNE metrics always; HOLDOUT only when `final` — every holdout look is logged, so the
    holdout set can't quietly become a tuning set."""
    out = {"progress": progress(db)}
    strata = dict(db.execute(select(BenchmarkQueue.domain, BenchmarkQueue.stratum)).tuples().all())
    splits = ("tune", "holdout") if final else ("tune",)
    if not final:
        out["holdout"] = "hidden: request a final evaluation to see holdout figures (each request is logged)"
    for split in splits:
        r = evaluate(db, split)
        labels = list(db.scalars(select(BenchmarkLabel).where(BenchmarkLabel.split == split)))
        per: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for lb in labels:
            per[strata.get(lb.domain, "other")][f"labelled_{lb.label or ('store' if lb.is_ecommerce else 'not_store')}"] += 1
        r["labels_by_stratum"] = {k: dict(v) for k, v in sorted(per.items())}
        out[split] = r
        if split == "holdout":
            from app.models import BenchmarkEvaluation

            db.add(BenchmarkEvaluation(split="holdout", requested_by=requested_by, labels=r["labels"],
                                       summary={k: r[k] for k in ("precision", "recall_decided", "false_positive_rate", "false_negative_rate")}))  # fmt: skip
            db.commit()
    from app.models import BenchmarkEvaluation

    out["holdout_evaluations_so_far"] = len(db.scalars(select(BenchmarkEvaluation.id).where(BenchmarkEvaluation.split == "holdout")).all())
    return out
