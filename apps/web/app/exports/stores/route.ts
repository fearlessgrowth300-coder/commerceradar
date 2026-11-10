import { type NextRequest } from "next/server";
import { apiFetch } from "@/lib/api";

// Rows per file; must match PART_ROWS in apps/api/app/services/workspace.py.
// One response has to stay under Vercel's 4.5 MB body limit.
const PART_ROWS: Record<string, number> = { domains: 100_000, full: 20_000 };

const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);

// Proxies the CSV export so the session token never reaches the browser.
// A download bigger than one file gets a small page of "Part 1, Part 2, …" links instead.
export async function GET(req: NextRequest) {
  const sp = new URLSearchParams(req.nextUrl.searchParams);
  const size = PART_ROWS[sp.get("columns") === "domains" ? "domains" : "full"];
  if (!sp.get("part")) {
    const filters = new URLSearchParams(sp);
    for (const k of ["limit", "columns", "part"]) filters.delete(k);
    filters.set("page_size", "1");
    const res = await apiFetch(`/api/v1/stores?${filters}`).catch(() => null);
    const total: number | undefined = res?.ok ? (await res.json()).total : undefined;
    const limit = Number(sp.get("limit")) || Infinity;
    const rows = total === undefined ? 0 : Math.min(total, limit);
    if (rows > size) {
      const parts = Math.ceil(rows / size);
      const links = Array.from({ length: parts }, (_, i) => {
        const p = new URLSearchParams(sp);
        p.set("part", String(i + 1));
        const from = i * size + 1;
        const to = Math.min(rows, (i + 1) * size);
        return `<li><a href="/exports/stores?${esc(p.toString())}">Part ${i + 1}</a> — stores ${from.toLocaleString("en-US")}–${to.toLocaleString("en-US")}</li>`;
      }).join("");
      return new Response(
        `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">` +
          `<title>Download stores</title><style>body{font:15px system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;color:#0f172a;background:#f8fafc}` +
          `li{margin:8px 0}a{color:#0369a1}</style></head><body><h1 style="font-size:20px">Your download is split into ${parts} files</h1>` +
          `<p>${rows.toLocaleString("en-US")} stores, up to ${size.toLocaleString("en-US")} per file. Click each part to download it.</p>` +
          `<ol>${links}</ol><p><a href="/stores">← Back to stores</a></p></body></html>`,
        { headers: { "content-type": "text/html; charset=utf-8" } },
      );
    }
  }
  const res = await apiFetch(`/api/v1/exports/stores.csv?${sp}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    return new Response(`Export not available: ${body.detail ?? res.status}`, { status: res.status, headers: { "content-type": "text/plain" } });
  }
  const part = sp.get("part");
  return new Response(res.body, {
    headers: {
      "content-type": "text/csv; charset=utf-8",
      "content-disposition": `attachment; filename="commerceradar-stores${part ? `-part${part}` : ""}.csv"`,
    },
  });
}
