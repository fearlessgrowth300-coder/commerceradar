"""CommerceRadar's own crawler: visits confirmed stores, follows a few internal links
(robots.txt respected, conservative delay, trap-resistant URL rules) and collects the
external sites they link to — stockists, partner brands, other shops — as new candidates.
One store per batch. A store isn't recrawled within `recrawl_days` (tracked as a
`link_crawl` store observation, which also keeps the history).
"""

import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser
from sqlalchemy import exists, select

from app.config import settings
from app.models import Domain, Store, StoreObservation
from app.security.ssrf import BlockedDestination, DnsFailure
from app.services.fetcher import FetchError
from app.services.verification import _robots
from app.sources import Batch, Ctx, Found

# Sites nearly every store links to; never candidates (host or any subdomain of these).
SKIP_DOMAINS = frozenset(
    """facebook.com fb.com fb.me instagram.com twitter.com x.com t.co tiktok.com youtube.com youtu.be
    pinterest.com linkedin.com snapchat.com threads.net whatsapp.com wa.me google.com goo.gl g.page
    apple.com microsoft.com amazon.com ebay.com etsy.com paypal.com stripe.com klarna.com afterpay.com
    shopify.com myshopify.com shop.app wordpress.com wordpress.org wix.com squarespace.com bigcommerce.com
    woocommerce.com cloudflare.com gstatic.com googleapis.com trustpilot.com yotpo.com judge.me okendo.io
    klaviyo.com mailchimp.com bit.ly linktr.ee spotify.com vimeo.com reddit.com medium.com tumblr.com
    discord.com discord.gg telegram.org t.me""".split()
)


def skip_host(host: str) -> bool:
    parts = host.split(".")
    return any(".".join(parts[i:]) in SKIP_DOMAINS for i in range(len(parts) - 1))


SKIP_PATH = re.compile(r"/(cart|checkout|account|login|register|search|wishlist|compare|cdn-cgi)(/|$)", re.I)
SKIP_EXT = re.compile(r"\.(jpe?g|png|gif|webp|svg|ico|pdf|zip|mp4|mp3|css|js|xml|json|txt)$", re.I)


def _same_site(host: str, store_host: str) -> bool:
    return host == store_host or host.endswith("." + store_host)


def crawlable(url: str, store_host: str) -> bool:
    """Internal link worth following: same site, short, no query-string explosions."""
    p = urlsplit(url)
    host = (p.hostname or "").removeprefix("www.")
    return (
        p.scheme in ("http", "https")
        and _same_site(host, store_host)
        and len(url) <= 200
        and len(p.query) <= 40
        and p.path.count("/") <= 4
        and not SKIP_PATH.search(p.path)
        and not SKIP_EXT.search(p.path)
    )


def links(html: str, page_url: str) -> list[str]:
    out = []
    for a in HTMLParser(html).css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if href and not href.startswith(("#", "mailto:", "tel:", "javascript:")):
            out.append(urljoin(page_url, href).split("#")[0])
    return out


class LinkCrawler:
    max_bytes = 2_000_000
    timeout = 10.0

    def fetch_batch(self, config: dict, cursor: dict, state: dict, ctx: Ctx) -> Batch:
        recrawl = datetime.now(UTC) - timedelta(days=int(config.get("recrawl_days", 30)))
        recent = exists().where(
            StoreObservation.store_id == Store.id,
            StoreObservation.observation_type == "link_crawl",
            StoreObservation.observed_at >= recrawl,
        )
        q = (
            select(Store.id, Domain.normalized_domain)
            .join(Domain, Domain.id == Store.domain_id)
            .where(Store.ecommerce_status == "confirmed_ecommerce", ~recent)
            .order_by(Store.id)
        )
        if cursor.get("after"):
            q = q.where(Store.id > uuid.UUID(cursor["after"]))
        row = ctx.db.execute(q.limit(1)).first()
        if row is None or cursor.get("sites", 0) >= int(config.get("max_sites_per_run", 50)):
            return Batch([], None, "no more stores due for crawling" if row is None else "site budget used")
        store_id, host = row

        external, pages = self.crawl(host, config, ctx)
        ctx.db.add(
            StoreObservation(
                store_id=store_id,
                observation_type="link_crawl",
                observation_data={"pages": pages, "external_hosts": len(external)},
                observed_at=datetime.now(UTC),
            )
        )
        nxt = {**cursor, "after": str(store_id), "sites": cursor.get("sites", 0) + 1}
        return Batch(list(external.values()), nxt, f"{host}: {len(pages)} pages, {len(external)} external hosts")

    def crawl(self, host: str, config: dict, ctx: Ctx) -> tuple[dict[str, Found], list[str]]:
        max_pages = int(config.get("max_pages_per_site", 5))
        delay = float(config.get("page_delay_seconds", 1.0))
        ua = settings.bot_user_agent
        base = f"https://{host}/"
        try:
            robots = _robots(ctx.fetcher, base)
        except (FetchError, BlockedDestination, DnsFailure):
            return {}, []
        queue, seen, pages = [base], {base}, []
        external: dict[str, Found] = {}
        while queue and len(pages) < max_pages:
            url = queue.pop(0)
            if not robots.can_fetch(ua, url):
                continue
            if pages:
                time.sleep(delay)  # conservative per-site rate
            try:
                r = ctx.fetcher.fetch(url)
            except (FetchError, BlockedDestination, DnsFailure):
                continue
            pages.append(url)
            if r.status >= 400 or "html" not in r.content_type:
                continue
            for link in links(r.text, r.url):
                lhost = (urlsplit(link).hostname or "").removeprefix("www.")
                if not lhost:
                    continue
                if _same_site(lhost, host):
                    if link not in seen and crawlable(link, host):
                        seen.add(link)
                        queue.append(link)
                elif not skip_host(lhost) and lhost not in external:
                    root = f"{urlsplit(link).scheme}://{urlsplit(link).netloc}/"
                    external[lhost] = Found(root)
        return external, pages
