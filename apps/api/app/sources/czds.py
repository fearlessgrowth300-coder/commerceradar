"""Newly registered domains from ICANN CZDS zone files, filtered by DNS to Shopify-hosted ones.

Daily flow per approved TLD (one discovery run, several batches):
  1. download <tld>.zone.gz once per UTC day (CZDS API, credentials from env, never stored in the DB)
  2. second-level names → sorted list on disk; `comm` against yesterday's list = today's NEW names.
     Shell pipeline (gzip/awk/sort/comm) on purpose: constant memory, also works for .com.
  3. resolve each new name's apex A record; keep only names inside `match_cidrs` (Shopify by default).
Only DNS matches are returned as candidates — the zone itself is never stored in the database,
and old zone files are deleted (see docs/source-rights.md). Every candidate is still verified.
The first download of a TLD is a baseline: nothing is "new" until the second day.
"""

import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from app.config import settings
from app.services.fetcher import FetchError
from app.sources import Batch, Ctx, Found, SourceError

AUTH_URL = "https://account-api.icann.org/api/authenticate"
API = "https://czds-api.icann.org"
DOWNLOADS = ("https://czds-api.icann.org/czds/downloads/", "https://czds-download-api.icann.org/czds/downloads/")
SHOPIFY = ["23.227.38.0/24"]
# Download + diff runs as a detached process: .com is ~5 GB and ~150 M names, far beyond the 5-minute
# Celery task limit. The run polls every PREPARE_POLL_SECONDS; a prepare older than PREPARE_TIMEOUT
# (e.g. the worker restarted and killed it) is started again.
PREPARE_POLL_SECONDS = 60
PREPARE_TIMEOUT = timedelta(hours=4)


def _read_status(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def prepare(tld: str, day: str, url: str, workdir: Path, client: httpx.Client | None = None) -> None:
    """Download today's zone and write the new-names list; records the outcome in prepare-<day>.json."""
    status = workdir / f"prepare-{day}.json"
    st = _read_status(status)
    own = client is None
    client = client or httpx.Client(timeout=600.0, follow_redirects=False)
    try:
        zone = workdir / f"{tld}-{day}.zone.gz"
        download(client, _token(client), url, zone)
        _, count, baseline = diff_zone(zone, tld, workdir, day)
        st.update(done=True, new=count, baseline=baseline, finished=datetime.now(UTC).isoformat())
    except Exception as e:  # reported to the run, which skips this TLD today
        st.update(error=f"{type(e).__name__}: {e}")
    finally:
        if own:
            client.close()
    status.write_text(json.dumps(st))


def launch_prepare(tld: str, day: str, url: str, workdir: Path, client: httpx.Client | None = None) -> None:
    """Start `prepare` detached (survives the Celery task). Tests replace this with a direct call."""
    log = (workdir / f"prepare-{day}.log").open("ab")
    subprocess.Popen([sys.executable, "-m", "app.sources.czds", tld, day, url, str(workdir)],
                     stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)  # fmt: skip


def zone_dir() -> Path:
    return Path(os.environ.get("ZONE_DIR", "/zones"))


def _token(client: httpx.Client) -> str:
    if not (settings.czds_username and settings.czds_password):
        raise SourceError("CZDS_USERNAME / CZDS_PASSWORD not configured")
    r = client.post(AUTH_URL, json={"username": settings.czds_username, "password": settings.czds_password})
    if r.status_code in (429, 503):
        raise FetchError(f"CZDS auth HTTP {r.status_code}")
    if r.status_code != 200:
        raise SourceError(f"CZDS auth HTTP {r.status_code}")
    return r.json()["accessToken"]


def approved_links(client: httpx.Client, token: str) -> dict[str, str]:
    """{tld: download url} for every zone this account is approved for."""
    r = client.get(f"{API}/czds/downloads/links", headers={"Authorization": f"Bearer {token}"})
    if r.status_code != 200:
        raise SourceError(f"CZDS links HTTP {r.status_code}")
    out = {}
    for url in r.json():
        if url.startswith(DOWNLOADS) and url.endswith(".zone"):  # never follow other hosts
            out[url.rsplit("/", 1)[-1].removesuffix(".zone")] = url
    return out


def download(client: httpx.Client, token: str, url: str, dest: Path) -> None:
    tmp = dest.with_suffix(".part")
    with client.stream("GET", url, headers={"Authorization": f"Bearer {token}"}) as r:
        if r.status_code in (429, 503):
            raise FetchError(f"CZDS download HTTP {r.status_code}")
        if r.status_code != 200:
            raise SourceError(f"CZDS download HTTP {r.status_code} for {url}")
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    tmp.replace(dest)


def diff_zone(zone_gz: Path, tld: str, workdir: Path, day: str) -> tuple[Path, int, bool]:
    """Write today's sorted second-level names and the names new since the previous list.
    Returns (new-names file, count, baseline?) and deletes older lists and the zone file."""
    today = workdir / f"domains-{day}.txt"
    new = workdir / f"new-{day}.txt"
    # owner names of NS records that are exactly <label>.<tld>  (zone lines: name ttl class type rdata)
    awk = f'tolower($4)=="ns"{{n=tolower($1); sub(/\\.$/,"",n); if (n ~ /^[a-z0-9-]+\\.{tld.replace(".", "[.]")}$/) print n}}'
    env = {**os.environ, "LC_ALL": "C"}
    subprocess.run(
        f"gzip -dc {_q(zone_gz)} | awk '{awk}' | nice -n 10 sort -u -S 100M -T {_q(workdir)} > {_q(today)}",
        shell=True, check=True, env=env,
    )  # fmt: skip
    prev = sorted(p for p in workdir.glob("domains-*.txt") if p.name < today.name)
    baseline = not prev
    if baseline:
        new.write_text("")
    else:
        subprocess.run(f"comm -13 {_q(prev[-1])} {_q(today)} > {_q(new)}", shell=True, check=True, env=env)
    for old in prev:
        old.unlink()
    for old in workdir.glob("new-*.txt"):
        if old != new:
            old.unlink()
    zone_gz.unlink(missing_ok=True)  # the raw zone is not kept
    with new.open() as f:
        count = sum(1 for _ in f)
    return new, count, baseline


def _q(p: Path) -> str:
    return "'" + str(p).replace("'", "'\\''") + "'"


def apex_ips(name: str) -> list[str]:
    try:
        return sorted({ai[4][0] for ai in socket.getaddrinfo(name, 443, socket.AF_INET, socket.SOCK_STREAM)})
    except (socket.gaierror, UnicodeError, OSError):
        return []


def matching(names: list[str], cidrs: list[str], threads: int = 32) -> list[tuple[str, str]]:
    nets = [ipaddress.ip_network(c) for c in cidrs]
    with ThreadPoolExecutor(max_workers=threads) as pool:
        res = list(pool.map(apex_ips, names))
    out = []
    for name, ips in zip(names, res, strict=True):
        hit = next((ip for ip in ips if any(ipaddress.ip_address(ip) in n for n in nets)), None)
        if hit:
            out.append((name, hit))
    return out


class Czds:
    max_bytes = 1_000_000  # unused: zone files are streamed to disk, not through SafeFetcher
    timeout = 600.0

    def fetch_batch(self, config: dict, cursor: dict, state: dict, ctx: Ctx, client: httpx.Client | None = None) -> Batch:
        day = cursor.get("day") or datetime.now(UTC).strftime("%Y-%m-%d")
        tlds = [t.lower().strip(".") for t in config.get("tlds") or []]
        i, off = int(cursor.get("i", 0)), int(cursor.get("off", 0))
        if i >= len(tlds):
            return Batch([], None, "all TLDs done")
        tld = tlds[i]
        workdir = zone_dir() / tld
        new_file = workdir / f"new-{day}.txt"
        base = {"day": day, "i": i}

        status = workdir / f"prepare-{day}.json"
        waiting = {**base, "off": 0, "wait": PREPARE_POLL_SECONDS}
        st = _read_status(status)
        if st and not st.get("done"):  # a background prepare exists for today
            if st.get("error"):
                status.unlink(missing_ok=True)  # tomorrow's run tries again
                return Batch([], {**base, "i": i + 1, "off": 0}, f".{tld}: download/diff failed ({st['error'][:200]}), skipped")
            if datetime.now(UTC) - datetime.fromisoformat(st["started"]) < PREPARE_TIMEOUT:
                return Batch([], waiting, f".{tld}: preparing (download + diff)")
            new_file.unlink(missing_ok=True)  # stale (worker restarted mid-prepare): start over
        if not new_file.exists():  # stage 1: download + diff (once per TLD per UTC day), in the background
            workdir.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(workdir).free < int(config.get("min_free_gb", 5)) << 30:
                raise SourceError(f"less than {config.get('min_free_gb', 5)} GB free in {workdir}")
            own = client is None
            client = client or httpx.Client(timeout=self.timeout, follow_redirects=False)
            try:
                links = approved_links(client, _token(client))
            finally:
                if own:
                    client.close()
            if tld not in links:
                return Batch([], {**base, "i": i + 1, "off": 0}, f".{tld}: not approved for this account (skipped)")
            status.write_text(json.dumps({"started": datetime.now(UTC).isoformat()}))
            launch_prepare(tld, day, links[tld], workdir, client=None if own else client)
            return Batch([], waiting, f".{tld}: download started")

        # stage 2: DNS-filter the new names, a slice per batch
        if off == 0 and st.get("done"):
            state.setdefault("last", {})[tld] = {"day": day, "new": st["new"], "baseline": st["baseline"]}
        per = int(config.get("dns_per_batch", 2000))
        with new_file.open() as f:
            names = [n.strip() for k, n in enumerate(f) if off <= k < off + per]
        if not names:
            status.unlink(missing_ok=True)
            st = (state.get("last") or {}).get(tld) or {}
            what = "baseline saved" if st.get("day") == day and st.get("baseline") else "done"
            return Batch([], {**base, "i": i + 1, "off": 0}, f".{tld}: {what}")
        hits = matching(names, config.get("match_cidrs") or SHOPIFY, int(config.get("dns_threads", 32)))
        found = [Found(url=f"https://{n}/", seen_at=datetime.now(UTC), ref=f"czds:{tld}:{day}:{ip}") for n, ip in hits]
        return Batch(found, {**base, "off": off + len(names)}, f".{tld}: {off + len(names)} resolved, {len(hits)} match")


if __name__ == "__main__":  # background prepare: python -m app.sources.czds <tld> <day> <url> <workdir>
    prepare(sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4]))
