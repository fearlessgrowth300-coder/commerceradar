"""urlscan.io search API — optional, supplementary.

Works without a key (30 searches/min per IP as of 2026-11); URLSCAN_API_KEY raises limits.
Default query "domain:myshopify.com" returns scans of pages that loaded from myshopify.com,
i.e. Shopify stores on their own domains. Results come newest-first; incremental runs stop
at the newest scan time seen by the previous run. Pages via `search_after`.
"""

import json
from datetime import datetime
from urllib.parse import urlencode

from app.config import settings
from app.sources import Batch, Ctx, Found, SourceError, get_ok


class UrlScan:
    max_bytes = 5_000_000
    timeout = 30.0

    def fetch_batch(self, config: dict, cursor: dict, state: dict, ctx: Ctx) -> Batch:
        query = config.get("query") or ""
        if not query:
            raise SourceError("config needs a urlscan search query")
        pages = cursor.get("pages", 0)
        if pages >= int(config.get("max_pages_per_run", 10)):
            state["since"] = cursor.get("newest", state.get("since"))
            return Batch([], None, "page budget used")

        params = {"q": query, "size": int(config.get("page_size", 100))}
        if cursor.get("search_after"):
            params["search_after"] = cursor["search_after"]
        headers = {"API-Key": settings.urlscan_api_key} if settings.urlscan_api_key else None
        r = get_ok(ctx.fetcher, "https://urlscan.io/api/v1/search/?" + urlencode(params), headers)
        try:
            data = json.loads(r.text)
            results = data["results"]
        except (ValueError, KeyError) as e:
            raise SourceError("unexpected urlscan response") from e

        since = state.get("since") or ""
        newest = cursor.get("newest") or ""
        found, reached_old = [], False
        for res in results:
            t = (res.get("task") or {}).get("time", "")
            if since and t and t <= since:
                reached_old = True
                break
            newest = max(newest, t)
            page = res.get("page") or {}
            if page.get("url"):
                try:
                    seen = datetime.fromisoformat(t.replace("Z", "+00:00")) if t else None
                except ValueError:
                    seen = None
                found.append(Found(page["url"], seen, f"urlscan:{res.get('_id') or (res.get('task') or {}).get('uuid', '')}"))

        more = data.get("has_more") and results and not reached_old
        if not more:
            state["since"] = newest or since
            return Batch(found, None, f"{len(found)} pages; finished")
        after = ",".join(str(x) for x in results[-1].get("sort", []))
        return Batch(found, {**cursor, "pages": pages + 1, "search_after": after, "newest": newest}, f"{len(found)} pages")
