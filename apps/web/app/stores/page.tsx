import Link from "next/link";
import { redirect } from "next/navigation";
import { apiError, apiFetch, fmtDate, getJson, PLATFORM_LABEL } from "@/lib/api";

export const dynamic = "force-dynamic";

type Row = {
  id: string;
  domain: string;
  store_name: string | null;
  primary_platform: string;
  platform_confidence: number | null;
  ecommerce_status: string;
  activity_status: string;
  first_verified_at: string | null;
  last_verified_at: string | null;
  product_count: number;
  business_country: string | null;
};
type Page = { items: Row[]; total: number; page: number; page_size: number };
type SignalType = { type: string; category: string; active: number };

const ACTIVITY = ["active", "recently_active", "inactive", "uncertain"];
const SORTS: [string, string][] = [
  ["newest", "Newest verified"],
  ["oldest", "Oldest verified"],
  ["last_verified", "Last verified"],
  ["name", "Name"],
  ["products", "Most products observed"],
];
const FILTERS = ["q", "platform", "activity", "country", "signal", "verified_from", "verified_to", "sort"] as const;

async function saveSearch(formData: FormData) {
  "use server";
  const filters = Object.fromEntries(FILTERS.map((k) => [k, String(formData.get(k) ?? "")]).filter(([, v]) => v));
  let error = "";
  try {
    const res = await apiFetch("/api/v1/searches", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: String(formData.get("name") || "Saved search"), filters, alerts_enabled: formData.get("alerts") === "on" }),
    });
    if (!res.ok) error = await apiError(res);
  } catch {
    error = "API unreachable";
  }
  redirect(error ? `/stores?${new URLSearchParams({ ...filters, error })}` : "/searches");
}

export default async function Stores({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const sp = await searchParams;
  const params = new URLSearchParams({ page: sp.page ?? "1", page_size: "50" });
  for (const k of FILTERS) if (sp[k]) params.set(k, sp[k]!);
  const [data, types] = await Promise.all([
    getJson<Page>(`/api/v1/stores?${params}`),
    getJson<SignalType[]>("/api/v1/signals/types"),
  ]);
  const pageNum = Number(sp.page ?? "1") || 1;
  const link = (page: number) => {
    const p = new URLSearchParams();
    for (const k of FILTERS) if (sp[k]) p.set(k, sp[k]!);
    p.set("page", String(page));
    return `/stores?${p}`;
  };

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Store directory</h1>
      <form className="grid grid-cols-1 gap-2 rounded-lg border border-slate-200 bg-white p-3 text-sm sm:grid-cols-4">
        <input name="q" defaultValue={sp.q} placeholder="Search domain or name" aria-label="Search" className="rounded-md border border-slate-300 px-2 py-1.5 sm:col-span-2" />
        <select name="platform" defaultValue={sp.platform ?? ""} aria-label="Platform" className="rounded-md border border-slate-300 px-2 py-1.5">
          <option value="">Any platform</option>
          {Object.entries(PLATFORM_LABEL).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        <select name="activity" defaultValue={sp.activity ?? ""} aria-label="Activity" className="rounded-md border border-slate-300 px-2 py-1.5">
          <option value="">Any activity</option>
          {ACTIVITY.map((a) => (
            <option key={a} value={a}>{a.replace("_", " ")}</option>
          ))}
        </select>
        <input
          name="country"
          defaultValue={sp.country}
          placeholder="Country code, e.g. US"
          aria-label="Business country (2-letter code)"
          pattern="[A-Za-z]{2}"
          maxLength={2}
          title="Two-letter country code, e.g. US, GB, CA"
          className="rounded-md border border-slate-300 px-2 py-1.5 uppercase"
        />
        <select name="signal" defaultValue={sp.signal ?? ""} aria-label="Signal" className="rounded-md border border-slate-300 px-2 py-1.5">
          <option value="">Any signal</option>
          {(types ?? []).map((t) => (
            <option key={t.type} value={t.type}>{t.type.replaceAll("_", " ")} ({t.active})</option>
          ))}
        </select>
        <label className="flex items-center gap-2 text-slate-600">
          Verified from <input type="date" name="verified_from" defaultValue={sp.verified_from} className="rounded-md border border-slate-300 px-2 py-1" />
        </label>
        <label className="flex items-center gap-2 text-slate-600">
          to <input type="date" name="verified_to" defaultValue={sp.verified_to} className="rounded-md border border-slate-300 px-2 py-1" />
        </label>
        <div className="flex gap-2">
          <select name="sort" defaultValue={sp.sort ?? "newest"} aria-label="Sort" className="flex-1 rounded-md border border-slate-300 px-2 py-1.5">
            {SORTS.map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
          <button className="rounded-md bg-slate-900 px-3 py-1.5 text-white">Apply</button>
        </div>
      </form>
      <p className="text-xs text-slate-500">Country filter is not offered: we have no reliable country data yet.</p>
      {sp.error && <p className="text-sm text-red-700">{sp.error}</p>}
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <form action={saveSearch} className="flex flex-wrap items-center gap-2">
          {FILTERS.map((k) => sp[k] && <input key={k} type="hidden" name={k} value={sp[k]} />)}
          <input name="name" required placeholder="Name this search" aria-label="Saved search name" className="rounded-md border border-slate-300 px-2 py-1" />
          <label className="flex items-center gap-1 text-slate-600"><input type="checkbox" name="alerts" /> alert me about new matches</label>
          <button className="rounded-md border border-slate-300 px-3 py-1">Save this search</button>
        </form>
        <a href={`/exports/stores?${new URLSearchParams(Object.fromEntries(FILTERS.filter((k) => sp[k]).map((k) => [k, sp[k]!])))}`} className="underline">
          Export CSV
        </a>
      </div>

      {!data ? (
        <p className="text-sm text-red-700">API unreachable.</p>
      ) : data.items.length === 0 ? (
        <p className="text-sm text-slate-500">No verified stores match these filters.</p>
      ) : (
        <>
          <p className="text-sm text-slate-600">{data.total.toLocaleString()} stores</p>
          <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-200 text-slate-500">
                <tr>
                  <th className="px-3 py-2 font-medium">Store</th>
                  <th className="px-3 py-2 font-medium">Platform</th>
                  <th className="px-3 py-2 font-medium">Activity</th>
                  <th className="px-3 py-2 font-medium" title="Business address set by the store (Shopify settings)">Country</th>
                  <th className="px-3 py-2 font-medium">Products observed</th>
                  <th className="px-3 py-2 font-medium">First verified</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((s) => (
                  <tr key={s.id} className="border-b border-slate-100 last:border-0">
                    <td className="px-3 py-2">
                      <Link href={`/stores/${s.id}`} className="font-medium hover:underline">{s.domain}</Link>
                      {s.store_name && <div className="max-w-xs truncate text-xs text-slate-500">{s.store_name}</div>}
                    </td>
                    <td className="px-3 py-2">{PLATFORM_LABEL[s.primary_platform] ?? s.primary_platform}</td>
                    <td className="px-3 py-2">{s.activity_status.replace("_", " ")}</td>
                    <td className="px-3 py-2">{s.business_country ?? "—"}</td>
                    <td className="px-3 py-2 tabular-nums">{s.product_count}</td>
                    <td className="px-3 py-2 text-slate-600">{fmtDate(s.first_verified_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex gap-4 text-sm">
            {pageNum > 1 && <Link href={link(pageNum - 1)}>← Previous</Link>}
            {pageNum * data.page_size < data.total && <Link href={link(pageNum + 1)}>Next →</Link>}
          </div>
        </>
      )}
    </div>
  );
}
