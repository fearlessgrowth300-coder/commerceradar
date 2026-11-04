import { notFound } from "next/navigation";
import { fmtDate, fmtPrice, getJson, type Product } from "@/lib/api";

export const dynamic = "force-dynamic";

type Observation = {
  observed_at: string;
  change_type: string;
  price: string | null;
  currency: string | null;
  availability: string | null;
  title: string | null;
  details: Record<string, [unknown, unknown]> | null;
};

export default async function ProductPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const data = await getJson<{ product: Product & { description: string | null }; history: Observation[] }>(
    `/api/v1/products/${encodeURIComponent(id)}`,
  );
  if (!data) notFound();
  const { product: p, history } = data;

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h1 className="text-lg font-semibold">{p.title ?? "Untitled product"}</h1>
        <a href={p.product_url} rel="noopener noreferrer nofollow" target="_blank" className="break-all text-sm text-slate-600 underline">
          {p.product_url}
        </a>
        <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
          <div><dt className="text-slate-500">Price</dt><dd className="tabular-nums">{fmtPrice(p.current_price, p.currency)}</dd></div>
          <div><dt className="text-slate-500">Availability</dt><dd>{p.availability ?? "—"}</dd></div>
          <div><dt className="text-slate-500">Status</dt><dd>{p.status}</dd></div>
          <div><dt className="text-slate-500">SKU</dt><dd>{p.sku ?? "—"}</dd></div>
          <div><dt className="text-slate-500">Category</dt><dd>{p.category ?? "—"}</dd></div>
          <div><dt className="text-slate-500">Source</dt><dd>{p.extraction_method ?? "—"}</dd></div>
          <div><dt className="text-slate-500">First seen</dt><dd>{fmtDate(p.first_seen_at)}</dd></div>
          <div><dt className="text-slate-500">Last seen</dt><dd>{fmtDate(p.last_seen_at)}</dd></div>
        </dl>
        {p.description && <p className="mt-3 line-clamp-4 text-sm text-slate-700">{p.description}</p>}
      </section>

      <section>
        <h2 className="mb-2 font-medium">History</h2>
        <ul className="space-y-2 text-sm">
          {history.map((o, i) => (
            <li key={i} className="rounded-md border border-slate-200 bg-white px-3 py-2">
              <span className="font-medium">{o.change_type}</span> · {fmtDate(o.observed_at)} · {fmtPrice(o.price, o.currency)} ·{" "}
              {o.availability ?? "—"}
              {o.details && Object.keys(o.details).length > 0 && (
                <span className="text-slate-600">
                  {" "}
                  — {Object.entries(o.details).map(([k, [a, b]]) => `${k}: ${String(a ?? "—")} → ${String(b ?? "—")}`).join("; ")}
                </span>
              )}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
