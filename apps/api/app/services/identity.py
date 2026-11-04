"""Store identity (M10 phase 2): one store, many domains — without false merges.

Model
  stores           one row per real storefront (stores.domain_id = current PRIMARY domain)
  store_domains    domain ↔ store links with role (primary | alias | suggested), alias_type,
                   confidence, evidence, first/last observed, valid_from/valid_to (history:
                   links are closed, never deleted). A domain belongs to at most one store at
                   a time (partial unique index); each store has exactly one current primary.

Evidence and what it is allowed to do (measured on production 2026-11-04):
  storefront_id      0.97  Same Shopify shop id (`Shopify.shop`) AND the response is served by
                           Shopify (Shopify IP block 23.227.38.0/24, Shopify cookies or headers).
                           The HTML id alone is NOT enough: 4 gambling/spam domains carried a
                           real store's id in copied HTML without being served by Shopify.
  platform_hosted    0.95  x.myshopify.com / x.mybigcommerce.com redirects to the merchant domain.
  redirect_same_site 0.90  shop.brand.com → brand.com (same registrable domain).
  redirect           0.80  brand-a.com → brand-b.com. Attributes the domain to the target store
                           but never merges two existing stores.
  canonical          0.60  <link rel=canonical>/og:url on another host: recorded as `suggested` only.
Existing stores are merged only at ≥ MERGE_MIN. Names, products or looks are never used.
Idempotent: re-applying the same evidence only refreshes last_observed_at.
"""

import ipaddress
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Domain, Store, StoreDomain, StoreObservation
from app.services.domains import InvalidDomain, normalize_domain

MERGE_MIN = 0.9
SHOPIFY_NET = ipaddress.ip_network("23.227.38.0/24")  # documented A-record target for Shopify custom domains
SHOPIFY_COOKIES = {"_shopify_y", "_shopify_s", "cart_currency", "localization", "_shopify_essential", "secure_customer_sig"}
PLATFORM_HOSTED = ("myshopify.com", "mybigcommerce.com")
# Never treat a redirect into these as "this domain is that store" (marketplaces, link hubs, platforms).
NOT_A_STORE_IDENTITY = {
    "amazon.com", "etsy.com", "ebay.com", "shopify.com", "shop.app", "linktr.ee", "instagram.com", "facebook.com",
    "tiktok.com", "google.com", "wix.com", "squarespace.com", "bigcommerce.com", "aliexpress.com", "temu.com",
}  # fmt: skip
CC_SLD = {"co.uk", "org.uk", "com.au", "net.au", "co.nz", "com.br", "co.jp", "co.za", "com.mx", "co.in", "com.tr", "com.cn"}


def registrable(host: str) -> str:
    """Approximate eTLD+1 (no public-suffix list dependency; common ccSLDs handled)."""
    labels = host.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in CC_SLD:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def platform_hosted(host: str) -> bool:
    return any(host == p or host.endswith("." + p) for p in PLATFORM_HOSTED)


def shopify_served(serving: dict) -> bool:
    ip = serving.get("ip")
    try:
        if ip and ipaddress.ip_address(ip) in SHOPIFY_NET:
            return True
    except ValueError:
        pass
    headers = serving.get("headers") or {}
    if headers.get("x-shopid") or headers.get("x-shopify-stage") or "shopify" in (headers.get("powered-by") or "").lower():
        return True
    return bool(SHOPIFY_COOKIES & set(serving.get("cookies") or []))


def storefront_key(signals: dict, serving: dict) -> str | None:
    shop = (signals or {}).get("shopify_shop")
    return f"shopify:{shop}" if shop and shopify_served(serving or {}) else None


def _host_of(url: str | None) -> str | None:
    try:
        return normalize_domain(urlsplit(url or "").hostname or "")
    except InvalidDomain:
        return None


def _domain(db: Session, host: str) -> Domain:
    d = db.scalar(select(Domain).where(Domain.normalized_domain == host))
    if d is None:
        d = Domain(normalized_domain=host)
        db.add(d)
        db.flush()
    return d


def current_link(db: Session, domain_id) -> StoreDomain | None:
    return db.scalar(
        select(StoreDomain).where(StoreDomain.domain_id == domain_id, StoreDomain.valid_to.is_(None),
                                  StoreDomain.role.in_(("primary", "alias")))
    )  # fmt: skip


def primary_store_of(db: Session, domain_id) -> Store | None:
    """The store this domain is the PRIMARY domain of (activity follows the primary only)."""
    link = current_link(db, domain_id)
    if link is not None:
        return db.get(Store, link.store_id) if link.role == "primary" else None
    return db.scalar(select(Store).where(Store.domain_id == domain_id, Store.merged_into_store_id.is_(None)))


def _store_for(db: Session, domain_id) -> Store | None:
    link = current_link(db, domain_id)
    if link is not None:
        return db.get(Store, link.store_id)
    return db.scalar(select(Store).where(Store.domain_id == domain_id))


def _log(db: Session, store: Store, kind: str, data: dict, now: datetime) -> None:
    db.add(StoreObservation(store_id=store.id, observation_type=kind, observation_data=data, observed_at=now))


def link(db: Session, store: Store, domain: Domain, role: str, alias_type: str, confidence: float, evidence: dict,
         now: datetime) -> StoreDomain:  # fmt: skip
    """Idempotent upsert of the current link domain → store. Moving a domain closes its old link."""
    cur = current_link(db, domain.id) if role != "suggested" else db.scalar(
        select(StoreDomain).where(StoreDomain.domain_id == domain.id, StoreDomain.store_id == store.id,
                                  StoreDomain.role == "suggested", StoreDomain.valid_to.is_(None)))  # fmt: skip
    if cur is not None and cur.store_id == store.id and cur.role == role:
        cur.last_observed_at = now
        if confidence > (cur.confidence or 0):
            cur.alias_type, cur.confidence, cur.evidence = alias_type, confidence, evidence
        return cur
    if cur is not None:
        cur.valid_to = now  # history kept
    row = StoreDomain(store_id=store.id, domain_id=domain.id, role=role, alias_type=alias_type, confidence=confidence,
                      evidence=evidence, first_observed_at=now, last_observed_at=now, valid_from=now)  # fmt: skip
    db.add(row)
    db.flush()
    if role != "primary":
        _log(db, store, "identity_link", {"domain": domain.normalized_domain, "role": role, "alias_type": alias_type,
                                          "confidence": confidence}, now)  # fmt: skip
    return row


def merge(db: Session, loser: Store, winner: Store, reason: dict, now: datetime) -> None:
    """Fold a duplicate store into another. Its own rows (products, observations) stay attached
    to it for history; it is hidden everywhere via merged_into_store_id."""
    if loser.id == winner.id or loser.merged_into_store_id:
        return
    loser.merged_into_store_id = winner.id
    for row in db.scalars(select(StoreDomain).where(StoreDomain.store_id == loser.id, StoreDomain.valid_to.is_(None))):
        row.valid_to = now
        if row.role in ("primary", "alias"):
            db.flush()
            d = db.get(Domain, row.domain_id)
            link(db, winner, d, "alias", "merged", reason.get("confidence", MERGE_MIN), {**reason, "from_store": str(loser.id)}, now)
    _log(db, winner, "identity_merge", {"merged_store_id": str(loser.id), **reason}, now)
    _log(db, loser, "identity_merged_into", {"store_id": str(winner.id), **reason}, now)


def _maybe_promote(db: Session, store: Store, domain: Domain, confidence: float, now: datetime) -> None:
    """A platform-hosted primary (x.myshopify.com) gives way to a strongly linked custom domain."""
    primary = db.get(Domain, store.domain_id)
    if not (primary and platform_hosted(primary.normalized_domain) and not platform_hosted(domain.normalized_domain)):
        return
    if confidence < MERGE_MIN or db.scalar(select(Store.id).where(Store.domain_id == domain.id, Store.id != store.id)):
        return  # the custom domain is still held by another (merged) store row: keep as alias
    old = current_link(db, primary.id)
    if old is not None:
        old.valid_to = now
    db.flush()
    link(db, store, primary, "alias", "platform_hosted", 0.95, {"previous_primary": True}, now)
    cur = current_link(db, domain.id)
    if cur is not None:
        cur.valid_to = now
    db.flush()
    link(db, store, domain, "primary", "promoted", confidence, {"replaced_primary": primary.normalized_domain}, now)
    store.domain_id = domain.id
    _log(db, store, "primary_domain_change", {"from": primary.normalized_domain, "to": domain.normalized_domain}, now)


def resolve(db: Session, domain: Domain, status: str, signals: dict, serving: dict, final_url: str | None,
            redirects: list[str], canonical: str | None, now: datetime) -> Store:  # fmt: skip
    """For a POSITIVE verification of `domain`: find or create the store and link the domain(s)."""
    host = domain.normalized_domain
    final = _host_of(final_url) or host
    key = storefront_key(signals, serving)
    ev = {"final_url": final_url, "redirects": redirects[:6], "ip": serving.get("ip"), "storefront_key": key}

    # Which domain does the storefront actually live on, and how strongly is `domain` tied to it?
    if final != host and registrable(final) not in NOT_A_STORE_IDENTITY:
        target_host = final
        if platform_hosted(host):
            alias_type, conf = "platform_hosted", 0.95
        elif registrable(final) == registrable(host):
            alias_type, conf = "redirect_same_site", 0.90
        else:
            alias_type, conf = "redirect", 0.80
    else:
        target_host, alias_type, conf = host, "self", 1.0
    target_domain = domain if target_host == host else _domain(db, target_host)

    def follow(st: Store | None) -> Store | None:
        return db.get(Store, st.merged_into_store_id) if st is not None and st.merged_into_store_id else st

    by_key = db.scalar(select(Store).where(Store.storefront_key == key, Store.merged_into_store_id.is_(None))) if key else None
    by_target = follow(_store_for(db, target_domain.id))
    by_self = follow(_store_for(db, domain.id))

    # A store that now redirects to an unrelated site with no store of its own: weak evidence,
    # so the store stays where it is and the target is only a suggestion (no move, no merge).
    if by_key is None and by_target is None and by_self is not None and target_host != host and conf < MERGE_MIN:
        link(db, by_self, target_domain, "suggested", alias_type, conf, ev, now)
        return by_self

    store = by_key or by_target or by_self
    if store is None:  # new storefront: it lives on the target host
        store = Store(domain_id=target_domain.id, first_verified_at=now, ecommerce_status=status, consecutive_failures=0)
        db.add(store)
        db.flush()
        link(db, store, target_domain, "primary", "self", 1.0, ev, now)
    elif current_link(db, store.domain_id) is None:  # legacy store created before identity links
        link(db, store, db.get(Domain, store.domain_id), "primary", "self", 1.0, {"backfill": True}, now)

    if key and not store.storefront_key and not db.scalar(select(Store.id).where(Store.storefront_key == key)):
        store.storefront_key = key

    # Fold duplicates — strong evidence only.
    strength = 0.97 if by_key is store else conf
    for other in (by_target, by_self):
        if other is not None and other.id != store.id and strength >= MERGE_MIN:
            merge(db, other, store, {"storefront_key": key, "alias_type": "storefront_id" if by_key is store else alias_type,
                                     "confidence": strength}, now)  # fmt: skip

    # Link both domains to the store (idempotent).
    for d, a_type, c in ((target_domain, "self" if target_domain.id == domain.id else "redirect_target", 1.0), (domain, alias_type, conf)):
        if by_key is store and a_type in ("self", "redirect_target") and d.id != store.domain_id:
            a_type, c = "storefront_id", 0.97
        cur = current_link(db, d.id)
        if d.id == store.domain_id:
            if cur is None or cur.store_id != store.id:
                link(db, store, d, "primary", "self", 1.0, ev, now)
            else:
                cur.last_observed_at = now
            continue
        if cur is not None and cur.store_id != store.id and c < MERGE_MIN:
            link(db, store, d, "suggested", a_type, c, ev, now)  # belongs elsewhere; don't steal it on weak evidence
            continue
        link(db, store, d, "alias", a_type, c, ev, now)
        _maybe_promote(db, store, d, c, now)

    # Canonical/og:url pointing elsewhere: recorded, never merged.
    c_host = _host_of(canonical)
    if c_host and c_host not in (host, target_host) and registrable(c_host) not in NOT_A_STORE_IDENTITY:
        link(db, store, _domain(db, c_host), "suggested", "canonical", 0.6, {"canonical": canonical}, now)
    return store
