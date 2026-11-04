"""Outbound HTTP for crawling. Every hop is SSRF-checked and the TCP connection goes to
the IP we validated (Host header + TLS SNI keep the real hostname), so DNS rebinding
between check and connect can't redirect us to an internal address."""

import ipaddress
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

from app.config import settings
from app.security.ssrf import ALLOWED_PORTS, BlockedDestination, Resolver, resolve_public, system_resolver

MAX_REDIRECTS = 5


class FetchError(Exception):
    """Transient network failure (timeout, connection refused, TLS error, 5xx). Worth retrying."""


@dataclass
class FetchResult:
    url: str  # final URL after redirects
    status: int
    content_type: str
    text: str
    truncated: bool
    redirects: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)  # final response, lower-cased names
    set_cookies: list[str] = field(default_factory=list)  # every Set-Cookie seen along the redirect chain


class SafeFetcher:
    def __init__(
        self,
        resolver: Resolver = system_resolver,
        transport: httpx.BaseTransport | None = None,
        max_bytes: int = 2_000_000,
    ):
        self.resolver = resolver
        self.max_bytes = max_bytes
        self.client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=False,
            trust_env=False,  # never route through env-configured proxies
            headers={"User-Agent": settings.bot_user_agent, "Accept": "text/html,application/xhtml+xml,*/*;q=0.5"},
        )

    def fetch(self, url: str) -> FetchResult:
        redirects: list[str] = []
        set_cookies: list[str] = []
        for _ in range(MAX_REDIRECTS + 1):
            resp, text, truncated = self._fetch_once(url)
            set_cookies += resp.headers.get_list("set-cookie")
            if resp.is_redirect and "location" in resp.headers:
                url = urljoin(url, resp.headers["location"])
                redirects.append(url)
                continue
            if resp.status_code >= 500:
                raise FetchError(f"HTTP {resp.status_code} from {url}")
            return FetchResult(
                url=url,
                status=resp.status_code,
                content_type=resp.headers.get("content-type", "").lower(),
                text=text,
                truncated=truncated,
                redirects=redirects,
                headers={k.lower(): v for k, v in resp.headers.items() if k.lower() != "set-cookie"},
                set_cookies=set_cookies,
            )
        raise BlockedDestination(f"more than {MAX_REDIRECTS} redirects")

    def _fetch_once(self, url: str) -> tuple[httpx.Response, str, bool]:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise BlockedDestination(f"unsupported URL {url!r}")
        if parts.username or parts.password:
            raise BlockedDestination("credentials in URL")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        if port not in ALLOWED_PORTS:
            raise BlockedDestination(f"port {port} not allowed")
        host = parts.hostname
        try:
            ipaddress.ip_address(host)
            raise BlockedDestination("IP-literal URLs not allowed")
        except ValueError:
            pass

        ip = resolve_public(host, self.resolver)[0]
        netloc = f"[{ip}]" if ":" in ip else ip
        if parts.port:
            netloc += f":{parts.port}"
        pinned = parts._replace(netloc=netloc).geturl()
        host_header = host if not parts.port else f"{host}:{parts.port}"

        try:
            with self.client.stream(
                "GET", pinned, headers={"Host": host_header}, extensions={"sni_hostname": host}
            ) as resp:
                body = bytearray()
                truncated = False
                for chunk in resp.iter_bytes():
                    body.extend(chunk)
                    if len(body) > self.max_bytes:
                        truncated = True
                        del body[self.max_bytes :]
                        break
                text = body.decode(resp.encoding or "utf-8", errors="replace")
                return resp, text, truncated
        except httpx.HTTPError as e:
            raise FetchError(f"{type(e).__name__} fetching {url}") from e
