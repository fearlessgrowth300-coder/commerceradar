"""Certificate Transparency via crt.sh JSON search.

crt.sh only answers narrow queries (TLD-wide patterns like "%.shop" return 502 at once), so
queries are per-organisation patterns: configured ones plus "%.<domain>" for every confirmed
store (finds shop./store./regional subdomains). One query per batch. Incremental: we keep
the highest certificate id seen per query and ignore anything not newer.
A certificate proves a hostname exists — never that it's a store; everything gets verified.
"""

import json
from datetime import UTC, datetime
from urllib.parse import quote

from sqlalchemy import select

from app.models import Domain, Store
from app.sources import Batch, Ctx, Found, SourceError, get_ok

# Hostnames that are mail/hosting plumbing, never storefronts.
INFRA_LABELS = {
    "mail", "smtp", "imap", "pop", "pop3", "mx", "webmail", "autodiscover", "autoconfig", "cpanel", "whm",
    "webdisk", "cpcalendars", "cpcontacts", "ftp", "ns1", "ns2", "vpn", "remote", "owa", "exchange",
    "lyncdiscover", "sip", "enterpriseenrollment", "enterpriseregistration", "dev", "staging", "test",
}  # fmt: skip


def hostnames(name_value: str) -> list[str]:
    out = []
    for name in name_value.lower().split("\n"):
        name = name.strip().removeprefix("*.")
        if not name or "@" in name or " " in name or "." not in name:
            continue
        if name.split(".", 1)[0] in INFRA_LABELS:
            continue
        out.append(name)
    return out


def _seen(entry: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(entry.get("not_before", "")).replace(tzinfo=UTC)
    except ValueError:
        return None


class CrtSh:
    max_bytes = 20_000_000
    timeout = 90.0  # crt.sh routinely takes 15-60s per query

    def queries(self, config: dict, ctx: Ctx) -> list[str]:
        qs = list(config.get("queries") or [])
        if config.get("seed_from_stores", True):
            confirmed = select(Domain.normalized_domain).join(Store, Store.domain_id == Domain.id)
            confirmed = confirmed.where(Store.ecommerce_status == "confirmed_ecommerce", Domain.suppressed_at.is_(None))
            confirmed = confirmed.order_by(Domain.normalized_domain)
            qs += [f"%.{d}" for d in ctx.db.scalars(confirmed.limit(int(config.get("max_store_queries", 200))))]
        return qs

    def fetch_batch(self, config: dict, cursor: dict, state: dict, ctx: Ctx) -> Batch:
        qs = self.queries(config, ctx)
        i = cursor.get("i", 0)
        if i >= len(qs):
            return Batch([], None, f"{len(qs)} queries done")
        q = qs[i]
        # Full history (no exclude=expired): with 90-day certificates, "earliest unexpired" is always
        # recent and would make every domain look new. Queries are per-domain, so history stays small.
        try:
            r = get_ok(ctx.fetcher, f"https://crt.sh/?q={quote(q)}&output=json&deduplicate=Y")
        except SourceError as e:  # one oversized / rejected query must not stop the run
            return Batch([], {**cursor, "i": i + 1}, f"{q}: skipped ({e})")
        try:
            entries = json.loads(r.text or "[]")
        except ValueError as e:
            raise SourceError(f"crt.sh returned non-JSON for {q!r}") from e

        last = state.get(q, 0)
        found: dict[str, Found] = {}
        newest = last
        for e in entries:
            cid = int(e.get("id") or 0)
            if cid <= last:
                continue
            newest = max(newest, cid)
            for h in hostnames(e.get("name_value", "")):
                seen = _seen(e)
                prev = found.get(h)
                if prev is None or (seen and (prev.seen_at is None or seen < prev.seen_at)):
                    found[h] = Found(f"https://{h}/", seen)  # keep the earliest certificate per hostname
        state[q] = newest
        return Batch(list(found.values()), {**cursor, "i": i + 1}, f"{q}: {len(entries)} certs, {len(found)} new hostnames")
