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
    body: bytes = b""  # raw bytes (e.g. gzip blocks); `text` is the decoded form


class SafeFetcher:
    def __init__(
        self,
        resolver: Resolver = system_resolver,
        transport: httpx.BaseTransport | None = None,
        max_bytes: int = 2_000_000,
        timeout: float = 10.0,
    ):
        self.resolver = resolver
        self.max_bytes = max_bytes
        self.client = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 15.0) if timeout > 10 else 5.0),
            follow_redirects=False,
            trust_env=False,  # never route through env-configured proxies
            headers={"User-Agent": settings.bot_user_agent, "Accept": "text/html,application/xhtml+xml,*/*;q=0.5"},
        )

    def fetch(self, url: str, headers: dict[str, str] | None = None) -> FetchResult:
        redirects: list[str] = []
        set_cookies: list[str] = []
        for _ in range(MAX_REDIRECTS + 1):
            resp, body, truncated = self._fetch_once(url, headers or {})
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
                text=body.decode(resp.encoding or "utf-8", errors="replace"),
                truncated=truncated,
                redirects=redirects,
                headers={k.lower(): v for k, v in resp.headers.items() if k.lower() != "set-cookie"},
                set_cookies=set_cookies,
                body=body,
            )
        raise BlockedDestination(f"more than {MAX_REDIRECTS} redirects")

    def _fetch_once(self, url: str, headers: dict[str, str]) -> tuple[httpx.Response, bytes, bool]:
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

        # Every address is already validated public. IPv4 first: many hosts (incl. Docker's default
        # network) have no IPv6 route, and DNS often lists AAAA first. Fall through on connect errors.
        ips = sorted(resolve_public(host, self.resolver), key=lambda a: ":" in a)
        host_header = host if not parts.port else f"{host}:{parts.port}"
        last: httpx.HTTPError | None = None
        for ip in ips:
            netloc = f"[{ip}]" if ":" in ip else ip
            if parts.port:
                netloc += f":{parts.port}"
            pinned = parts._replace(netloc=netloc).geturl()
            try:
                with self.client.stream(
                    "GET", pinned, headers={**headers, "Host": host_header}, extensions={"sni_hostname": host}
                ) as resp:
                    body = bytearray()
                    truncated = False
                    for chunk in resp.iter_bytes():
                        body.extend(chunk)
                        if len(body) > self.max_bytes:
                            truncated = True
                            del body[self.max_bytes :]
                            break
                    return resp, bytes(body), truncated
            except httpx.ConnectError as e:
                last = e  # unreachable address: try the next one
            except httpx.HTTPError as e:
                raise FetchError(f"{type(e).__name__} fetching {url}") from e
        raise FetchError(f"{type(last).__name__} fetching {url} (tried {len(ips)} addresses)") from last
