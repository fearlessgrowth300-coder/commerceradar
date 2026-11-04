import Link from "next/link";
import { fmtDate, getJson } from "@/lib/api";

export const dynamic = "force-dynamic";

type SignalType = { type: string; category: string; basis: string; active: number };
type Row = {
  store_id: string;
  domain: string;
  store_name: string | null;
  signal_type: string;
  category: string;
  basis: string;
  summary: string;
  first_detected_at: string;
};

export default async function Signals({ searchParams }: { searchParams: Promise<{ type?: string; category?: string; page?: string }> }) {
  const sp = await searchParams;
  const params = new URLSearchParams({ page: sp.page ?? "1", page_size: "50" });
  if (sp.type) params.set("type", sp.type);
  if (sp.category) params.set("category", sp.category);
  const [types, data] = await Promise.all([
    getJson<SignalType[]>("/api/v1/signals/types"),
    getJson<{ items: Row[]; total: number; page_size: number }>(`/api/v1/signals?${params}`),
  ]);
  const page = Number(sp.page ?? "1") || 1;
  const pageLink = (n: number) => `/signals?${new URLSearchParams({ ...(sp.type ? { type: sp.type } : {}), ...(sp.category ? { category: sp.category } : {}), page: String(n) })}`;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Signals</h1>
        <p className="text-sm text-slate-600">
          Each signal is something we measured (<span className="text-emerald-700">observed</span>) or an interpretation we flag as such
          (<span className="text-amber-700">inferred</span>). None of them say whether a business is profitable or making sales.
        </p>
      </div>
      <div className="flex flex-wrap gap-2 text-sm">
        <Link href="/signals" className={!sp.type && !sp.category ? "font-semibold underline" : "text-slate-600"}>All</Link>
        {(types ?? []).map((t) => (
          <Link key={t.type} href={`/signals?type=${t.type}`} className={sp.type === t.type ? "font-semibold underline" : "text-slate-600"}>
            {t.type.replaceAll("_", " ")} ({t.active})
          </Link>
        ))}
      </div>
      {!data ? (
        <p className="text-sm text-red-700">API unreachable.</p>
      ) : data.items.length === 0 ? (
        <p className="text-sm text-slate-500">No active signals of this kind.</p>
      ) : (
        <ul className="space-y-2">
          {data.items.map((r) => (
            <li key={`${r.store_id}-${r.signal_type}`} className="rounded-lg border border-slate-200 bg-white p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Link href={`/stores/${r.store_id}`} className="font-medium hover:underline">{r.domain}</Link>
                <span className="text-slate-500">{r.signal_type.replaceAll("_", " ")}</span>
                <span className="rounded border border-slate-200 px-1.5 text-xs">{r.category}</span>
                <span className={`rounded border px-1.5 text-xs ${r.basis === "inferred" ? "border-amber-200 bg-amber-50 text-amber-800" : "border-emerald-200 bg-emerald-50 text-emerald-800"}`}>
                  {r.basis}
                </span>
                <span className="text-xs text-slate-500">since {fmtDate(r.first_detected_at)}</span>
              </div>
              <p className="mt-1 text-slate-700">{r.summary}</p>
            </li>
          ))}
        </ul>
      )}
      {data && (
        <div className="flex gap-4 text-sm">
          {page > 1 && <Link href={pageLink(page - 1)}>← Previous</Link>}
          {page * data.page_size < data.total && <Link href={pageLink(page + 1)}>Next →</Link>}
        </div>
      )}
    </div>
  );
}
