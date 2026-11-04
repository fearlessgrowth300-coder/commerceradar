"""Operator commands. Usage (inside the api container):

    python -m app.cli create-admin you@example.com [--org "Name"]

Creates a platform admin with a random password, written to stdout once. Nothing is stored
in plain text; change it after first login if you like (re-run with a new email to add more).
"""

import argparse
import secrets
import sys

from app.db import SessionLocal
from app.services import accounts


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-admin")
    c.add_argument("email")
    c.add_argument("--org", default=None)
    b = sub.add_parser("benchmark-sample", help="blind stratified sample CSV for human labelling")
    b.add_argument("--n", type=int, default=200)
    b.add_argument("--seed", type=int, default=1)
    b = sub.add_parser("benchmark-import", help="import labelled CSV (domain,is_ecommerce,platform,notes)")
    b.add_argument("file")
    b.add_argument("--label-source", required=True)
    b.add_argument("--labeller", required=True)
    b = sub.add_parser("benchmark-eval", help="precision/recall on a split (default: holdout)")
    b.add_argument("--split", default="tune", choices=("tune", "holdout"))
    b.add_argument("--final", action="store_true", help="required for --split holdout (each holdout look is logged)")
    b.add_argument("--live", action="store_true", help="re-verify labelled domains now instead of using stored verdicts")
    b = sub.add_parser("benchmark-queue", help="fill the blind labelling queue (stratified, default 200)")
    b.add_argument("--batch", default="m11-1")
    b.add_argument("--seed", type=int, default=11)
    b = sub.add_parser("probe-shells", help="read-only: what static evidence do JS-only pages expose?")
    b.add_argument("--limit", type=int, default=50)
    args = p.parse_args(argv)

    if args.cmd == "create-admin":
        password = secrets.token_urlsafe(18)
        with SessionLocal() as db:
            try:
                accounts.register(db, args.email, password, None, args.org, is_admin=True)
            except accounts.AuthError as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
        print(f"admin created: {args.email}")
        print(f"password: {password}")
    elif args.cmd == "benchmark-sample":
        from app.services import benchmark

        with SessionLocal() as db:
            sys.stdout.write(benchmark.sample_csv(db, args.n, args.seed))
    elif args.cmd == "benchmark-import":
        from app.services import benchmark

        with SessionLocal() as db, open(args.file, encoding="utf-8") as f:
            print(benchmark.import_csv(db, f.read(), args.label_source, args.labeller))
    elif args.cmd == "benchmark-eval":
        import json

        from app.services import benchmark

        if args.split == "holdout" and not args.final:
            print("refusing: holdout figures are for the final report only; add --final (the look is logged)", file=sys.stderr)
            return 2
        with SessionLocal() as db:
            if args.split == "holdout":
                from app.models import BenchmarkEvaluation

                db.add(BenchmarkEvaluation(split="holdout", requested_by="cli", labels=0))
                db.commit()
            preds = _live_predictions(db, args.split) if args.live else None
            print(json.dumps(benchmark.evaluate(db, args.split, preds), indent=2, default=str))
    elif args.cmd == "benchmark-queue":
        from app.services import benchmark

        with SessionLocal() as db:
            print(benchmark.build_queue(db, args.batch, args.seed))
    elif args.cmd == "probe-shells":
        import json

        with SessionLocal() as db:
            print(json.dumps(probe_shells(db, args.limit), indent=2))
    return 0


def _live_predictions(db, split: str) -> dict:
    """Fresh, read-only verification of labelled domains (nothing is written to the database)."""
    from sqlalchemy import select

    from app.models import BenchmarkLabel
    from app.services import suppression, verification
    from app.services.fetcher import FetchError, SafeFetcher

    out = {}
    for lb in db.scalars(select(BenchmarkLabel).where(BenchmarkLabel.split == split)):
        f = SafeFetcher()
        f.blocked = suppression.guard(db)
        try:
            r = verification.verify(lb.domain, f)
            out[lb.domain] = (r.status, r.platform.platform if r.platform else None)
        except FetchError:
            out[lb.domain] = ("temporarily_unreachable", None)
        finally:
            f.client.close()
    return out


def probe_shells(db, limit: int) -> dict:
    """M10 phase 6 investigation (read-only, robots-respecting): for pages we marked 'uncertain'
    because they render nothing without JS, how often do sitemaps or platform fingerprints give
    static evidence? Nothing is written; no classifier change is made from this."""
    from collections import Counter

    from sqlalchemy import select

    from app.config import settings
    from app.models import CrawlJob, Domain
    from app.security.ssrf import BlockedDestination, DnsFailure
    from app.services import platforms, products, suppression
    from app.services.fetcher import FetchError, SafeFetcher
    from app.services.verification import _robots

    latest = (
        select(CrawlJob.domain_id, CrawlJob.result)
        .where(CrawlJob.job_type == "verify", CrawlJob.status == "succeeded")
        .order_by(CrawlJob.domain_id, CrawlJob.completed_at.desc())
        .distinct(CrawlJob.domain_id)
        .subquery()
    )
    rows = db.execute(
        select(Domain.normalized_domain, latest.c.result).join(latest, latest.c.domain_id == Domain.id)
        .where(Domain.verification_status == "uncertain", Domain.suppressed_at.is_(None))
    ).all()  # fmt: skip
    shells = [d for d, r in rows if "no server-rendered content" in (((r or {}).get("evidence") or {}).get("reason") or "")]
    tally, details = Counter(), []
    for host in shells[:limit]:
        f = SafeFetcher()
        f.blocked = suppression.guard(db)
        rec = {"domain": host}
        try:
            base = f"https://{host}/"
            robots = _robots(f, base)
            if not robots.can_fetch(settings.bot_user_agent, base):
                tally["robots_disallow"] += 1
                continue
            home = f.fetch(base)
            pf = platforms.detect(home.text, home.headers, platforms.cookie_names(home.set_cookies), False)
            stats = products.ExtractStats()
            urls = products._sitemaps(f, base, robots, host, stats)
            rec.update(platform=pf.platform, platform_confidence=pf.confidence, sitemap_product_urls=len(urls))
            tally["probed"] += 1
            tally["platform_fingerprint"] += pf.platform not in ("unknown", "custom")
            tally["sitemap_products"] += len(urls) > 0
            tally["either"] += pf.platform not in ("unknown", "custom") or len(urls) > 0
            tally["both"] += pf.platform not in ("unknown", "custom") and len(urls) > 0
        except (FetchError, BlockedDestination, DnsFailure) as e:
            tally["fetch_failed"] += 1
            rec["error"] = type(e).__name__
        finally:
            f.client.close()
        details.append(rec)
    return {"shells_total": len(shells), "probed_limit": limit, "tally": dict(tally), "details": details}


if __name__ == "__main__":
    raise SystemExit(main())
