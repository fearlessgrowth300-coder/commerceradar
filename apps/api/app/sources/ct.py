"""Certificate Transparency logs: domains that just got a TLS certificate, filtered by DNS to Shopify-hosted ones.

Public RFC 6962 logs (get-entries), read forward from where the last run stopped (`state.positions[log]`).
A log's first run starts at its current end: history is not replayed. Each certificate's DNS names →
registrable domain → skip names already in `domains` → apex A record inside `match_cidrs` → candidate.
Like every source, a candidate is only a candidate: verification decides what becomes a store.

Logs are sharded by certificate EXPIRY year (nimbus2026 = certs expiring in 2026, …) and shards are
disjoint, so config lists several. ponytail: shard names are config — add next year's shard (and drop
the expired one) once a year; automate from the Google log list if that gets forgotten.
"""

import base64
import json
from datetime import UTC, datetime

from sqlalchemy import select

from app.models import Domain
from app.services.fetcher import FetchError
from app.services.identity import registrable
from app.sources import Batch, Ctx, Found, SourceError
from app.sources.czds import SHOPIFY, matching

SAN_OID = b"\x06\x03\x55\x1d\x11"
SKIP = ("myshopify.com", "shopifypreview.com", "amazonaws.com", "aws.dev", "a2z.org.cn", "cloudflare", "azure",
        "herokuapp.com", "vercel.app", "netlify.app")  # fmt: skip


def _len(b: bytes, i: int) -> tuple[int, int]:
    n = b[i]
    if n < 0x80:
        return n, i + 1
    k = n & 0x7F
    return int.from_bytes(b[i + 1 : i + 1 + k], "big"), i + 1 + k


def dns_names(der: bytes) -> list[str]:
    """dNSName entries of the subjectAltName extension (DER walk; no X.509 library needed)."""
    i = der.find(SAN_OID)
    if i < 0:
        return []
    try:
        i += len(SAN_OID)
        if der[i] == 0x01:  # critical BOOLEAN
            i += 3
        if der[i] != 0x04:
            return []
        _, i = _len(der, i + 1)
        if der[i] != 0x30:
            return []
        n, i = _len(der, i + 1)
        end, out = i + n, []
        while i < end:
            tag = der[i]
            n, j = _len(der, i + 1)
            if tag == 0x82:  # [2] dNSName
                out.append(der[j : j + n].decode("ascii", "ignore").lower())
            i = j + n
        return out
    except IndexError:
        return []


def entry_cert(entry: dict) -> bytes:
    """Certificate (or precertificate) DER of one get-entries item."""
    leaf = base64.b64decode(entry["leaf_input"])
    if int.from_bytes(leaf[10:12], "big") == 0:  # x509_entry: cert is in the leaf
        return leaf[15 : 15 + int.from_bytes(leaf[12:15], "big")]
    extra = base64.b64decode(entry["extra_data"])  # precert_entry: PrecertChainEntry starts with the precert
    return extra[3 : 3 + int.from_bytes(extra[0:3], "big")]


def domains_of(entries: list[dict]) -> list[str]:
    out: dict[str, None] = {}
    for e in entries:
        try:
            names = dns_names(entry_cert(e))
        except (KeyError, ValueError):
            continue
        for n in names:
            n = n.removeprefix("*.").strip(".")
            if not n or any(s in n for s in SKIP):
                continue
            reg = registrable(n)  # ponytail: approximate eTLD+1; unknown ccSLDs (com.sg…) just fail the DNS match
            if "." in reg:
                out[reg] = None
    return list(out)


class CertTransparency:
    max_bytes = 20_000_000  # one get-entries page (≤1000 entries) is ~7 MB
    timeout = 60.0

    def fetch_batch(self, config: dict, cursor: dict, state: dict, ctx: Ctx) -> Batch:
        logs = [u.rstrip("/") + "/" for u in config.get("logs") or []]
        if not logs:
            raise SourceError("config.logs is empty")
        pages, page = int(config.get("pages_per_batch", 16)), int(config.get("page_size", 1000))
        positions = dict(state.get("positions") or {})
        entries, caught_up, notes = [], 0, []
        for log in logs:
            if log not in positions:  # first time: start at the end, don't replay history
                r = ctx.fetcher.fetch(log + "ct/v1/get-sth")
                if r.status != 200:
                    raise FetchError(f"get-sth HTTP {r.status} from {log}")
                positions[log] = int(json.loads(r.text)["tree_size"])
            got = 0
            for _ in range(pages):  # logs may return fewer than asked (Cloudflare: ≤256): short ≠ end
                pos = positions[log]
                r = ctx.fetcher.fetch(f"{log}ct/v1/get-entries?start={pos}&end={pos + page - 1}")
                if r.status == 400:  # past the log's end
                    caught_up += 1
                    break
                if r.status != 200:
                    raise FetchError(f"get-entries HTTP {r.status} from {log}")
                batch = json.loads(r.text).get("entries") or []
                if not batch:
                    caught_up += 1
                    break
                entries += batch
                positions[log] = pos + len(batch)
                got += len(batch)
            notes.append(f"{log.split('/')[-2] or log}: +{got}")
        state["positions"] = positions  # new dict: JSON column change is detected

        names = domains_of(entries)
        known: set[str] = set()
        for k in range(0, len(names), 1000):
            known |= set(ctx.db.scalars(select(Domain.normalized_domain).where(Domain.normalized_domain.in_(names[k : k + 1000]))))
        fresh = [n for n in names if n not in known]
        hits = matching(fresh, config.get("match_cidrs") or SHOPIFY, int(config.get("dns_threads", 64)))
        now = datetime.now(UTC)
        found = [Found(url=f"https://{n}/", seen_at=now, ref=f"ct:{ip}") for n, ip in hits]
        note = f"{', '.join(notes)} entries; {len(names)} domains, {len(fresh)} new, {len(hits)} match"
        done = caught_up == len(logs)
        return Batch(found, None if done else {"n": int(cursor.get("n", 0)) + 1}, note + ("; caught up" if done else ""))
