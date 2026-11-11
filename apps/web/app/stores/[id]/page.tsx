import Link from "next/link";
import { notFound } from "next/navigation";
import { fmtDate, fmtPrice, getJson, PLATFORM_LABEL, type Product } from "@/lib/api";

export const dynamic = "force-dynamic";

type Signal = {
  signal_type: string;
  category: string;
  basis: string;
  summary: string;
  evidence: Record<string, unknown>;
  active: boolean;
  first_detected_at: string;
  last_detected_at: string;
  resolved_at: string | null;
};
type Detail = {
  store: {
    id: string;
    store_name: string | null;
    canonical_url: string | null;
    ecommerce_status: string;
    activity_status: string;
    activity_confidence: number | null;
    activity_evidence: Record<string, unknown> | null;
    primary_platform: string;
    platform_confidence: number | null;
    business_country?: string | null;
    business_region?: string | null;
    business_city?: string | null;
    location_checked_at?: string | null;
    language?: string | null;
    published_products?: number | null;
    first_product_at?: string | null;
    latest_product_at?: string | null;
    category_group?: string | null;
    category?: string | null;
    category_confidence?: number | null;
    is_pod?: boolean | null;
    is_cod?: boolean | null;
    is_dropshipping?: boolean | null;
    is_digital?: boolean | null;
    catalog_checked_at?: string | null;
    first_verified_at: string | null;
    last_verified_at: string | null;
    last_successful_check_at: string | null;
    last_failed_check_at: string | null;
    next_check_at: string | null;
    last_changed_at: string | null;
  };
  domain: string;
  domain_id: string;
  domain_first_seen_at: string;
  product_count: number;
  detections: { platform: string; confidence: number; detection_method: string; detected_at: string; evidence: Record<string, unknown> }[];
  signals: Signal[];
  sources: { name: string; source_type: string; first_discovered_at: string; last_discovered_at: string; urls: number }[];
};
type Observation = { observation_type: string; observation_data: Record<string, unknown>; observed_at: string };

const pct = (v: number | null) => (v == null ? "" : ` (${Math.round(v * 100)}%)`);

export default async function StorePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const sid = encodeURIComponent(id);
  const [d, obs, products] = await Promise.all([
    getJson<Detail>(`/api/v1/stores/${sid}`),
    getJson<{ items: Observation[]; total: number }>(`/api/v1/stores/${sid}/observations?page_size=30`),
    getJson<{ items: Product[]; total: number }>(`/api/v1/stores/${sid}/products?page_size=20`),
  ]);
  if (!d) notFound();
  const s = d.store;
  const active = d.signals.filter((x) => x.active);
  const past = d.signals.filter((x) => !x.active);

  return (
    <div className="space-y-6">
      <Link href="/stores" className="text-sm text-slate-600">← Store directory</Link>
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h1 className="text-lg font-semibold">{s.store_name ?? d.domain}</h1>
        <p className="text-sm text-slate-600">
          {d.domain} · <Link href={`/domains/${d.domain_id}`} className="underline">verification checks</Link>
        </p>
        <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
          <div><dt className="text-slate-500">Platform</dt><dd>{(PLATFORM_LABEL[s.primary_platform] ?? s.primary_platform) + pct(s.platform_confidence)}</dd></div>
          <div><dt className="text-slate-500">Activity</dt><dd>{s.activity_status.replace("_", " ") + pct(s.activity_confidence)}</dd></div>
          <div><dt className="text-slate-500">Classification</dt><dd>{s.ecommerce_status.replace("_", " ")}</dd></div>
          <div>
            <dt className="text-slate-500" title="Self-reported: the business address the store set in its Shopify settings. Not proof of where the owner lives.">
              Business country (set by store)
            </dt>
            <dd>
              {s.business_country
                ? [s.business_city, s.business_region, s.business_country].filter(Boolean).join(", ")
                : s.location_checked_at ? "not published" : "not checked yet"}
            </dd>
          </div>
          <div><dt className="text-slate-500">Language</dt><dd className="uppercase">{s.language ?? (s.catalog_checked_at ? "not declared" : "not checked yet")}</dd></div>
          <div>
            <dt className="text-slate-500" title="Inferred from product types, tags and titles">Category (inferred)</dt>
            <dd>{s.category_group ? [s.category_group, s.category].filter(Boolean).join(" › ") + pct(s.category_confidence ?? null) : s.catalog_checked_at ? "not enough evidence" : "not checked yet"}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Product format</dt>
            <dd>
              {[s.is_dropshipping && "Dropshipping (inferred)", s.is_pod && "Print on demand (inferred)", s.is_cod && "Cash on delivery (inferred)", s.is_digital && "Digital products"].filter(Boolean).join(", ") || (s.catalog_checked_at ? "none detected" : "not checked yet")}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500" title="Shopify does not publish a store's opening date; this is the oldest listed product we saw">Store created (first product)</dt>
            <dd>{fmtDate(s.first_product_at ?? null)}</dd>
          </div>
          <div><dt className="text-slate-500">Latest product added</dt><dd>{fmtDate(s.latest_product_at ?? null)}</dd></div>
          <div><dt className="text-slate-500">Published products (Shopify list)</dt><dd>{s.published_products ?? "—"}</dd></div>
          <div><dt className="text-slate-500">First discovered</dt><dd>{fmtDate(d.domain_first_seen_at)}</dd></div>
          <div><dt className="text-slate-500">First verified</dt><dd>{fmtDate(s.first_verified_at)}</dd></div>
          <div><dt className="text-slate-500">Last verified</dt><dd>{fmtDate(s.last_verified_at)}</dd></div>
          <div><dt className="text-slate-500">Last change observed</dt><dd>{fmtDate(s.last_changed_at)}</dd></div>
          <div><dt className="text-slate-500">Next check</dt><dd>{fmtDate(s.next_check_at)}</dd></div>
          <div><dt className="text-slate-500">Products observed</dt><dd>{d.product_count}</dd></div>
        </dl>
        {typeof s.activity_evidence?.rule === "string" && <p className="mt-2 text-sm text-slate-600">Activity basis: {s.activity_evidence.rule}</p>}
        <p className="mt-1 text-xs text-slate-500">An active website does not mean the store is making sales. We do not estimate revenue or traffic.</p>
      </section>

      <section>
        <h2 className="mb-2 font-medium">Signals</h2>
        {active.length === 0 ? (
          <p className="text-sm text-slate-500">No active signals.</p>
        ) : (
          <ul className="space-y-2">
            {active.map((x) => (
              <li key={x.signal_type} className="rounded-lg border border-slate-200 bg-white p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{x.signal_type.replaceAll("_", " ")}</span>
                  <span className="rounded border border-slate-200 px-1.5 text-xs text-slate-600">{x.category}</span>
                  <span className={`rounded border px-1.5 text-xs ${x.basis === "inferred" ? "border-amber-200 bg-amber-50 text-amber-800" : "border-emerald-200 bg-emerald-50 text-emerald-800"}`}>
                    {x.basis}
                  </span>
                  <span className="text-xs text-slate-500">since {fmtDate(x.first_detected_at)}</span>
                </div>
                <p className="mt-1 text-slate-700">{x.summary}</p>
                <details className="mt-1">
                  <summary className="cursor-pointer text-xs text-slate-500">Evidence</summary>
                  <pre className="mt-1 overflow-auto rounded bg-slate-50 p-2 text-xs">{JSON.stringify(x.evidence, null, 2)}</pre>
                </details>
              </li>
            ))}
          </ul>
        )}
        {past.length > 0 && (
          <p className="mt-2 text-xs text-slate-500">
            Resolved: {past.map((x) => `${x.signal_type.replaceAll("_", " ")} (${fmtDate(x.resolved_at)})`).join(", ")}
          </p>
        )}
      </section>

      <section>
        <h2 className="mb-2 font-medium">Products ({d.product_count})</h2>
        {!products || products.items.length === 0 ? (
          <p className="text-sm text-slate-500">No products observed yet.</p>
        ) : (
          <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white text-sm">
            {products.items.map((p) => (
              <li key={p.id} className="flex flex-wrap justify-between gap-2 px-3 py-2">
                <Link href={`/products/${p.id}`} className="hover:underline">{p.title ?? p.product_url}</Link>
                <span className="tabular-nums text-slate-600">{fmtPrice(p.current_price, p.currency)} · {p.availability ?? "—"}</span>
              </li>
            ))}
          </ul>
        )}
        {products && products.total > 20 && (
          <Link href={`/domains/${d.domain_id}`} className="mt-1 inline-block text-sm underline">All products</Link>
        )}
      </section>

      <section>
        <h2 className="mb-2 font-medium">Platform detection evidence</h2>
        {d.detections.length === 0 ? (
          <p className="text-sm text-slate-500">No detections yet.</p>
        ) : (
          <details className="rounded-lg border border-slate-200 bg-white p-3 text-sm">
            <summary className="cursor-pointer">
              Latest: {PLATFORM_LABEL[d.detections[0].platform] ?? d.detections[0].platform} · {d.detections[0].detection_method} ·{" "}
              {fmtDate(d.detections[0].detected_at)}
            </summary>
            <pre className="mt-2 max-h-80 overflow-auto rounded bg-slate-50 p-2 text-xs">{JSON.stringify(d.detections[0].evidence, null, 2)}</pre>
          </details>
        )}
      </section>

      <section>
        <h2 className="mb-2 font-medium">Source history</h2>
        {d.sources.length === 0 ? (
          <p className="text-sm text-slate-500">Submitted manually (no discovery source record).</p>
        ) : (
          <ul className="text-sm text-slate-700">
            {d.sources.map((x) => (
              <li key={x.name}>
                {x.name} ({x.source_type}) · first {fmtDate(x.first_discovered_at)} · last {fmtDate(x.last_discovered_at)} · {x.urls} URL(s)
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="mb-2 font-medium">Historical observations</h2>
        {!obs || obs.items.length === 0 ? (
          <p className="text-sm text-slate-500">No observations yet.</p>
        ) : (
          <ul className="space-y-1 text-sm">
            {obs.items.map((o, i) => (
              <li key={i} className="rounded-md border border-slate-200 bg-white px-3 py-1.5">
                <span className="font-medium">{o.observation_type.replaceAll("_", " ")}</span>{" "}
                <span className="text-slate-500">{fmtDate(o.observed_at)}</span>{" "}
                <span className="break-all text-xs text-slate-600">{JSON.stringify(o.observation_data).slice(0, 220)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
