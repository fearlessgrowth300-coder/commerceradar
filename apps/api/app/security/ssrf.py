"""SSRF guard: only public, globally-routable destinations on ports 80/443."""

import ipaddress
import socket
from collections.abc import Callable

ALLOWED_PORTS = {80, 443}


class BlockedDestination(Exception):
    """Destination is not a public internet host. Never retried."""


class DnsFailure(Exception):
    """Hostname does not resolve."""


Resolver = Callable[[str], list[str]]


def system_resolver(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise DnsFailure(f"{host}: {e}") from e
    return sorted({info[4][0] for info in infos})


def is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%", 1)[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    # is_global excludes private, loopback, link-local (incl. 169.254.169.254 metadata),
    # CGNAT 100.64/10, unique-local fc00::/7, documentation and reserved ranges.
    return addr.is_global and not addr.is_multicast


def resolve_public(host: str, resolver: Resolver = system_resolver) -> list[str]:
    """Resolve host; refuse if ANY address is non-public (blocks split-horizon tricks)."""
    ips = resolver(host)
    if not ips:
        raise DnsFailure(f"{host}: no addresses")
    bad = [ip for ip in ips if not is_public_ip(ip)]
    if bad:
        raise BlockedDestination(f"{host} resolves to non-public address {bad[0]}")
    return ips
