import { type NextRequest } from "next/server";
import { apiFetch } from "@/lib/api";

// Proxies the CSV export so the session token never reaches the browser.
export async function GET(req: NextRequest) {
  const res = await apiFetch(`/api/v1/exports/stores.csv?${req.nextUrl.searchParams}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    return new Response(`Export not available: ${body.detail ?? res.status}`, { status: res.status, headers: { "content-type": "text/plain" } });
  }
  return new Response(res.body, {
    headers: {
      "content-type": "text/csv; charset=utf-8",
      "content-disposition": res.headers.get("content-disposition") ?? 'attachment; filename="stores.csv"',
    },
  });
}
