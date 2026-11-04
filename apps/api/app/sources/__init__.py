"""Discovery source adapters (spec §7). Every adapter implements one call:

    fetch_batch(config, cursor, state, ctx) -> Batch

`cursor` is where this run resumes (stored on the run, so an interrupted run continues);
`state` survives across runs (incremental collection) — adapters mutate it in place.
Adapters only *find* URLs. `app.services.discovery.ingest` dedupes them into
`discovered_urls` / `domains` and queues verification; adapters never touch `stores`.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.services.fetcher import FetchError, FetchResult, SafeFetcher


class SourceError(Exception):
    """Permanent problem with the source (bad config, 4xx, unparseable data). Not retried."""


@dataclass
class Found:
    url: str
    seen_at: datetime | None = None  # when the source observed it, if it says


@dataclass
class Batch:
    found: list[Found]
    cursor: dict[str, Any] | None  # None ⇒ run finished
    note: str = ""  # human-readable progress for the run log


@dataclass
class Ctx:
    fetcher: SafeFetcher
    db: Session
    log: list[str] = field(default_factory=list)


class Source(Protocol):
    max_bytes: int  # response cap for this source's fetcher
    timeout: float  # seconds; public data services can be slow

    def fetch_batch(self, config: dict, cursor: dict, state: dict, ctx: Ctx) -> Batch: ...


def get_ok(fetcher: SafeFetcher, url: str, headers: dict[str, str] | None = None) -> FetchResult:
    """GET that turns rate limits into retryable errors and other 4xx into permanent ones."""
    r = fetcher.fetch(url, headers)
    if r.status in (408, 425, 429):
        raise FetchError(f"HTTP {r.status} (rate limited) from {url}")
    if r.status >= 400:
        raise SourceError(f"HTTP {r.status} from {url}")
    if r.truncated:
        raise SourceError(f"response from {url} exceeded {fetcher.max_bytes} bytes")
    return r


def registry() -> dict[str, Source]:
    from app.sources import commoncrawl, crawler, crtsh, urlscan

    return {
        "commoncrawl": commoncrawl.CommonCrawl(),
        "crtsh": crtsh.CrtSh(),
        "crawler": crawler.LinkCrawler(),
        "urlscan": urlscan.UrlScan(),
    }
