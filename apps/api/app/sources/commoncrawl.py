"""Common Crawl via its raw ZipNum CDX index on data.commoncrawl.org.

The public CDX query server (index.commoncrawl.org) times out on most queries, so we read
the index files directly — the same files that server reads:
  cluster.idx  sorted "SURT timestamp\\tcdx-NNNNN.gz\\toffset\\tlength\\tseq" lines, one per block
  cdx-NNNNN.gz concatenated gzip members; each member (block) ≈3000 sorted CDX lines
We binary-search cluster.idx with small Range requests, then fetch only the blocks whose
SURT keys fall under the configured host suffixes (e.g. "shop" ⇒ every *.shop host).
No bulk downloads: ~30 × 8 KB to locate a suffix, then ~250 KB per block.
"""

import gzip
import json
import re
from datetime import UTC, datetime

from app.services.fetcher import FetchError
from app.sources import Batch, Ctx, Found, SourceError, get_ok

BASE = "https://data.commoncrawl.org/cc-index/collections/{index}/indexes/"
PROBE = 8192
INDEX_ID = re.compile(r"^CC-MAIN-\d{4}-\d{2}$")
SUFFIX = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*$")


def surt_prefix(suffix: str) -> str:
    """'shop' → 'shop,' ; 'co.uk' → 'uk,co,' (matches every host under the suffix)."""
    return ",".join(reversed(suffix.lower().strip(".").split("."))) + ","


def _lines(ctx: Ctx, url: str, offset: int, size: int = PROBE) -> list[tuple[int, str]]:
    """Complete lines in [offset, offset+size), with their byte offsets. Skips the partial first line."""
    r = ctx.fetcher.fetch(url, {"Range": f"bytes={offset}-{offset + size - 1}"})
    if r.status == 416:  # offset at/after end of file
        return []
    if r.status in (408, 425, 429, 503):
        raise FetchError(f"HTTP {r.status} (rate limited) from {url}")
    if r.status != 206:
        raise SourceError(f"HTTP {r.status} for range request to {url}")
    data = r.body
    pos = 0
    if offset > 0:
        nl = data.find(b"\n")
        if nl < 0:
            return []
        pos = nl + 1
    out = []
    while True:
        nl = data.find(b"\n", pos)
        if nl < 0:
            return out
        out.append((offset + pos, data[pos:nl].decode("utf-8", "replace")))
        pos = nl + 1


def _file_size(ctx: Ctx, url: str) -> int:
    r = get_ok(ctx.fetcher, url, {"Range": "bytes=0-0"})
    m = re.search(r"/(\d+)$", r.headers.get("content-range", ""))
    if not m:
        raise SourceError(f"no Content-Range from {url}; range requests unsupported?")
    return int(m.group(1))


def find_start(ctx: Ctx, cluster_url: str, prefix: str) -> int:
    """Byte offset of the last cluster.idx line whose key sorts before `prefix` (its block may
    already contain matching keys), or 0."""
    lo, hi = 0, _file_size(ctx, cluster_url)
    while hi - lo > PROBE:
        mid = (lo + hi) // 2
        first = _lines(ctx, cluster_url, mid)
        if first and first[0][1].split(" ", 1)[0] < prefix:
            lo = first[0][0]
        else:
            hi = mid
    start = 0
    for off, line in _lines(ctx, cluster_url, lo, 2 * PROBE + 1024):
        if line.split(" ", 1)[0] >= prefix:
            break
        start = off
    return start


def parse_block(raw: bytes, prefix: str) -> tuple[list[Found], bool]:
    """→ (one HTML URL per host in this block, past_prefix). `past_prefix` means the sorted
    keys have moved beyond the suffix, so later blocks can't match."""
    by_host: dict[str, Found] = {}
    past = False
    for line in gzip.decompress(raw).decode("utf-8", "replace").splitlines():
        key, _, rest = line.partition(" ")
        if not key.startswith(prefix):
            if key > prefix:
                past = True
                break
            continue
        ts, _, meta = rest.partition(" ")
        try:
            rec = json.loads(meta)
        except ValueError:
            continue
        if rec.get("status") != "200" or "html" not in rec.get("mime-detected", rec.get("mime", "")):
            continue
        host = key.split(")", 1)[0]
        # ponytail: one URL per host per block is enough to find the domain; product/category URLs come in M5.
        if host not in by_host:
            try:
                seen = datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
            except ValueError:
                seen = None
            by_host[host] = Found(rec["url"], seen)
    return list(by_host.values()), past


class CommonCrawl:
    max_bytes = 4_000_000

    def fetch_batch(self, config: dict, cursor: dict, state: dict, ctx: Ctx) -> Batch:
        index = config.get("index", "")
        suffixes = config.get("host_suffixes") or []
        if not INDEX_ID.match(index) or not suffixes or not all(SUFFIX.match(s) for s in suffixes):
            raise SourceError("config needs index like CC-MAIN-2026-43 and host_suffixes like ['shop']")
        blocks_per_batch = int(config.get("blocks_per_batch", 3))
        max_blocks = int(config.get("max_blocks_per_run", 50))

        i = cursor.get("suffix_i", 0)
        while i < len(suffixes) and state.get(f"{index}:{suffixes[i]}") == "done":
            i += 1
        if i >= len(suffixes) or cursor.get("blocks", 0) >= max_blocks:
            return Batch([], None, "all configured suffixes done" if i >= len(suffixes) else "block budget used")

        suffix, prefix = suffixes[i], surt_prefix(suffixes[i])
        key = f"{index}:{suffix}"
        cluster_url = BASE.format(index=index) + "cluster.idx"
        offset = state.get(key)
        if not isinstance(offset, int):
            offset = find_start(ctx, cluster_url, prefix)  # resume point persists across runs

        found: list[Found] = []
        done = False
        lines = _lines(ctx, cluster_url, offset)
        for n, (off, line) in enumerate(lines[:blocks_per_batch]):
            parts = line.split("\t")
            if len(parts) < 4:
                raise SourceError(f"unexpected cluster.idx line: {line[:120]!r}")
            if n > 0 and parts[0].split(" ", 1)[0] > prefix and not parts[0].startswith(prefix):
                done = True
                break
            cdx_url = BASE.format(index=index) + parts[1]
            start, length = int(parts[2]), int(parts[3])
            block = get_ok(ctx.fetcher, cdx_url, {"Range": f"bytes={start}-{start + length - 1}"})
            urls, past = parse_block(block.body, prefix)
            found += urls
            offset = lines[n + 1][0] if n + 1 < len(lines) else off + len(line.encode()) + 1
            if past:
                done = True
                break
        else:
            if not lines:
                done = True  # end of index

        state[key] = "done" if done else offset
        blocks = cursor.get("blocks", 0) + blocks_per_batch
        nxt = {**cursor, "suffix_i": i + 1 if done else i, "blocks": blocks}
        return Batch(found, nxt, f"{index} *.{suffix}: {len(found)} hosts" + (" (suffix done)" if done else ""))
