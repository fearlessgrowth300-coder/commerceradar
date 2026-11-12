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
  language: string | null;
  category_group: string | null;
  category: string | null;
  formats: string[];
  published_products: number | null;
  first_product_at: string | null;
  latest_product_at: string | null;
};
type Category = { group: string; subcategories: string[] };
type Page = { items: Row[]; total: number; page: number; page_size: number };
type SignalType = { type: string; category: string; active: number };

const ACTIVITY = ["active", "recently_active", "inactive", "uncertain"];
const SORTS: [string, string][] = [
  ["newest", "Newest verified"],
  ["oldest", "Oldest verified"],
  ["last_verified", "Last verified"],
  ["name", "Name"],
  ["products", "Most products observed"],
  ["latest_product", "Newest product added"],
  ["store_created", "Newest store (first product)"],
];
const LANGUAGES: [string, string][] = [
  ["en", "English"], ["es", "Spanish"], ["fr", "French"], ["de", "German"], ["pt", "Portuguese"], ["it", "Italian"],
  ["nl", "Dutch"], ["sv", "Swedish"], ["da", "Danish"], ["nb", "Norwegian"], ["fi", "Finnish"], ["pl", "Polish"],
  ["cs", "Czech"], ["ro", "Romanian"], ["hu", "Hungarian"], ["el", "Greek"], ["tr", "Turkish"], ["ru", "Russian"],
  ["uk", "Ukrainian"], ["ar", "Arabic"], ["he", "Hebrew"], ["hi", "Hindi"], ["ja", "Japanese"], ["ko", "Korean"],
  ["zh", "Chinese"], ["id", "Indonesian"], ["ms", "Malay"], ["th", "Thai"], ["vi", "Vietnamese"],
];
// Every ISO 3166 two-letter code the runtime can name, minus pseudo-regions (EU, UN, ZZ…), sorted by name.
const NOT_COUNTRIES = new Set(["AC", "CP", "CQ", "DG", "EA", "EU", "EZ", "IC", "QO", "TA", "UN", "XA", "XB", "ZZ"]);
const COUNTRIES: [string, string][] = (() => {
  const names = new Intl.DisplayNames(["en"], { type: "region" });
  const out: [string, string][] = [];
  for (let a = 65; a <= 90; a++)
    for (let b = 65; b <= 90; b++) {
      const code = String.fromCharCode(a, b);
      const name = names.of(code);
      if (name && name !== code && !NOT_COUNTRIES.has(code)) out.push([code, name]);
    }
  return out.sort((x, y) => x[1].localeCompare(y[1]));
})();
const FORMATS: [string, string][] = [
  ["dropshipping", "Dropshipping (inferred)"], ["pod", "Print on demand (inferred)"],
  ["cod", "Cash on delivery (inferred)"], ["digital", "Digital products"],
];
const FORMAT_SHORT: Record<string, string> = { dropshipping: "Dropship", pod: "POD", cod: "COD", digital: "Digital" };
const FILTERS = [
  "q", "platform", "activity", "country", "language", "category", "format", "signal", "verified_from", "verified_to",
  "first_product_from", "first_product_to", "latest_product_from", "latest_product_to", "sort",
] as const;

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
  const [data, types, categories] = await Promise.all([
    getJson<Page>(`/api/v1/stores?${params}`),
    getJson<SignalType[]>("/api/v1/signals/types"),
    getJson<Category[]>("/api/v1/categories"),
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
        <select name="country" defaultValue={sp.country?.toUpperCase() ?? ""} aria-label="Business country" className="rounded-md border border-slate-300 px-2 py-1.5">
          <option value="">Any country</option>
          {COUNTRIES.map(([code, name]) => (
            <option key={code} value={code}>{name}</option>
          ))}
        </select>
        <select name="signal" defaultValue={sp.signal ?? ""} aria-label="Signal" className="rounded-md border border-slate-300 px-2 py-1.5">
          <option value="">Any signal</option>
          {(types ?? []).map((t) => (
            <option key={t.type} value={t.type}>{t.type.replaceAll("_", " ")} ({t.active})</option>
          ))}
        </select>
        <select name="language" defaultValue={sp.language ?? ""} aria-label="Store language" className="rounded-md border border-slate-300 px-2 py-1.5">
          <option value="">Any language</option>
          {LANGUAGES.map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        <select name="category" defaultValue={sp.category ?? ""} aria-label="Category (inferred)" className="rounded-md border border-slate-300 px-2 py-1.5">
          <option value="">Any category</option>
          {(categories ?? []).map((c) => (
            <optgroup key={c.group} label={c.group}>
              <option value={c.group}>All {c.group}</option>
              {c.subcategories.map((s) => (
                <option key={`${c.group}-${s}`} value={s}>{s}</option>
              ))}
            </optgroup>
          ))}
        </select>
        <select name="format" defaultValue={sp.format ?? ""} aria-label="Product format" className="rounded-md border border-slate-300 px-2 py-1.5">
          <option value="">Any product format</option>
          {FORMATS.map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        <label className="flex items-center gap-2 text-slate-600" title="Shopify does not publish a store's opening date; this uses the date its oldest listed product was added">
          Store created (first product) from <input type="date" name="first_product_from" defaultValue={sp.first_product_from} className="rounded-md border border-slate-300 px-2 py-1" />
        </label>
        <label className="flex items-center gap-2 text-slate-600">
          to <input type="date" name="first_product_to" defaultValue={sp.first_product_to} className="rounded-md border border-slate-300 px-2 py-1" />
        </label>
        <label className="flex items-center gap-2 text-slate-600">
          Latest product added from <input type="date" name="latest_product_from" defaultValue={sp.latest_product_from} className="rounded-md border border-slate-300 px-2 py-1" />
        </label>
        <label className="flex items-center gap-2 text-slate-600">
          to <input type="date" name="latest_product_to" defaultValue={sp.latest_product_to} className="rounded-md border border-slate-300 px-2 py-1" />
        </label>
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
      <p className="text-xs text-slate-500">
        Language, product dates and &quot;digital&quot; are read from the store itself. Category, dropshipping, print on demand and cash on
        delivery are inferred from products and installed apps, so treat them as a guide. Sales, revenue and traffic are not shown: there is
        no reliable public source for them.
      </p>
      {sp.error && <p className="text-sm text-red-700">{sp.error}</p>}
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <form action={saveSearch} className="flex flex-wrap items-center gap-2">
          {FILTERS.map((k) => sp[k] && <input key={k} type="hidden" name={k} value={sp[k]} />)}
          <input name="name" required placeholder="Name this search" aria-label="Saved search name" className="rounded-md border border-slate-300 px-2 py-1" />
          <label className="flex items-center gap-1 text-slate-600"><input type="checkbox" name="alerts" /> alert me about new matches</label>
          <button className="rounded-md border border-slate-300 px-3 py-1">Save this search</button>
        </form>
        {/* Download: same filters as the list above, newest first unless another sort is chosen */}
        <form action="/exports/stores" method="get" className="flex flex-wrap items-center gap-2">
          {FILTERS.map((k) => sp[k] && <input key={k} type="hidden" name={k} value={sp[k]} />)}
          <label className="flex items-center gap-1 text-slate-600">
            Download
            <input
              type="number"
              name="limit"
              min={1}
              step={1}
              defaultValue={Math.min(1000, data?.total ?? 1000) || 1000}
              aria-label="Number of stores to download"
              className="w-28 rounded-md border border-slate-300 px-2 py-1"
            />
            stores
          </label>
          <select name="columns" defaultValue="domains" aria-label="File contents" className="rounded-md border border-slate-300 px-2 py-1">
            <option value="domains">domains only</option>
            <option value="full">full details</option>
          </select>
          <label className="flex items-center gap-1 text-slate-600" title="Your team's earlier downloads are remembered; ticked = only stores nobody on your account has downloaded yet">
            <input type="checkbox" name="new_only" value="1" defaultChecked /> skip stores already downloaded
          </label>
          <button className="rounded-md bg-slate-900 px-3 py-1 text-white">Download CSV</button>
          {data && <span className="text-xs text-slate-500">{data.total.toLocaleString("en-US")} match these filters</span>}
        </form>
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
                  <th className="px-3 py-2 font-medium">Language</th>
                  <th className="px-3 py-2 font-medium" title="Inferred from products">Category</th>
                  <th className="px-3 py-2 font-medium">Format</th>
                  <th className="px-3 py-2 font-medium" title="Oldest listed product (store-created proxy) and newest product">Products added</th>
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
                    <td className="px-3 py-2 uppercase">{s.language ?? "—"}</td>
                    <td className="px-3 py-2 text-xs">{s.category ?? s.category_group ?? "—"}{s.category && s.category_group && <div className="text-slate-500">{s.category_group}</div>}</td>
                    <td className="px-3 py-2 text-xs">{s.formats.length ? s.formats.map((f) => FORMAT_SHORT[f] ?? f).join(", ") : "—"}</td>
                    <td className="px-3 py-2 text-xs text-slate-600">{s.first_product_at ? `${fmtDate(s.first_product_at)} → ${fmtDate(s.latest_product_at)}` : "—"}</td>
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
