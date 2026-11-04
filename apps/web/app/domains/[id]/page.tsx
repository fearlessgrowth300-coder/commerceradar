import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { StatusBadge } from "@/components/StatusBadge";
import { API_URL, type Domain, fmtDate, fmtPrice, getJson, type Job, PLATFORM_LABEL, type Product } from "@/lib/api";

export const dynamic = "force-dynamic";

type Detail = {
  domain: Domain;
  jobs: Job[];
  store_id: string | null;
  platform: string | null;
  platform_confidence: number | null;
  activity: {
    activity_status: string;
    activity_confidence: number | null;
    activity_evidence: Record<string, unknown> | null;
    consecutive_failures: number;
    last_successful_check_at: string | null;
    last_failed_check_at: string | null;
    next_check_at: string | null;
  } | null;
};

async function extract(formData: FormData) {
  "use server";
  const storeId = String(formData.get("store_id"));
  const back = `/domains/${encodeURIComponent(String(formData.get("domain_id")))}`;
  let error = "";
  try {
    const res = await fetch(`${API_URL}/api/v1/stores/${encodeURIComponent(storeId)}/products/extract`, {
      method: "POST",
      cache: "no-store",
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      error = typeof body.detail === "string" ? body.detail : `request failed (${res.status})`;
    }
  } catch {
    error = "API unreachable";
  }
  redirect(error ? `${back}?error=${encodeURIComponent(error)}` : back);
}

export default async function DomainPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ error?: string }>;
}) {
  const { id } = await params;
  const { error } = await searchParams;
  const data = await getJson<Detail>(`/api/v1/domains/${encodeURIComponent(id)}`);
  if (!data) notFound();
  const { domain, jobs, store_id, platform, platform_confidence, activity } = data;
  const inFlight = jobs.some((j) => ["queued", "running", "retrying"].includes(j.status));
  const products = store_id
    ? await getJson<{ items: Product[]; total: number }>(`/api/v1/stores/${encodeURIComponent(store_id)}/products?page_size=100`)
    : null;

  return (
    <div className="space-y-6">
      {inFlight && <meta httpEquiv="refresh" content="5" />}
      <Link href="/discovery" className="text-sm text-slate-600">← Discovery</Link>
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h1 className="text-lg font-semibold break-all">{domain.normalized_domain}</h1>
        <dl className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          <div><dt className="text-slate-500">Verification</dt><dd><StatusBadge status={domain.verification_status} /></dd></div>
          <div>
            <dt className="text-slate-500">Confidence</dt>
            <dd>{domain.verification_confidence == null ? "—" : `${Math.round(domain.verification_confidence * 100)}%`}</dd>
          </div>
          <div><dt className="text-slate-500">DNS / resolution</dt><dd>{domain.current_resolution_status ?? "—"}</dd></div>
          <div><dt className="text-slate-500">Verified store record</dt><dd>{store_id ? "Yes" : "No"}</dd></div>
          <div>
            <dt className="text-slate-500">Platform</dt>
            <dd>
              {platform ? `${PLATFORM_LABEL[platform] ?? platform}${platform_confidence ? ` (${Math.round(platform_confidence * 100)}%)` : ""}` : "—"}
            </dd>
          </div>
          <div><dt className="text-slate-500">First seen</dt><dd>{fmtDate(domain.first_seen_at)}</dd></div>
          <div><dt className="text-slate-500">Last checked</dt><dd>{fmtDate(domain.last_checked_at)}</dd></div>
        </dl>
        {activity && (
          <div className="mt-4 border-t border-slate-100 pt-3 text-sm">
            <p>
              <span className="text-slate-500">Activity:</span>{" "}
              <span className="font-medium">{activity.activity_status.replace("_", " ")}</span>
              {activity.activity_confidence != null && ` (${Math.round(activity.activity_confidence * 100)}%)`}
              {typeof activity.activity_evidence?.rule === "string" && (
                <span className="text-slate-600"> — {activity.activity_evidence.rule}</span>
              )}
            </p>
            <p className="mt-1 text-slate-600">
              Last success {fmtDate(activity.last_successful_check_at)} · last failure {fmtDate(activity.last_failed_check_at)} ·
              consecutive failures {activity.consecutive_failures} · next check {fmtDate(activity.next_check_at)}
            </p>
            <p className="mt-1 text-xs text-slate-500">An active website does not mean the store is making sales.</p>
          </div>
        )}
      </section>

      {store_id && (
        <section>
          <div className="mb-2 flex flex-wrap items-center gap-3">
            <h2 className="font-medium">Products {products ? `(${products.total.toLocaleString()})` : ""}</h2>
            <form action={extract}>
              <input type="hidden" name="store_id" value={store_id} />
              <input type="hidden" name="domain_id" value={domain.id} />
              <button className="rounded-md bg-slate-900 px-3 py-1 text-sm text-white">Extract products</button>
            </form>
            {error && <span className="text-sm text-red-700">{error}</span>}
          </div>
          {!products || products.items.length === 0 ? (
            <p className="text-sm text-slate-500">No products observed yet.</p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-slate-200 text-slate-500">
                  <tr>
                    <th className="px-3 py-2 font-medium">Product</th>
                    <th className="px-3 py-2 font-medium">Price</th>
                    <th className="px-3 py-2 font-medium">Availability</th>
                    <th className="px-3 py-2 font-medium">Status</th>
                    <th className="px-3 py-2 font-medium">Last seen</th>
                  </tr>
                </thead>
                <tbody>
                  {products.items.map((p) => (
                    <tr key={p.id} className="border-b border-slate-100 last:border-0">
                      <td className="max-w-md px-3 py-2">
                        <Link href={`/products/${p.id}`} className="hover:underline">{p.title ?? p.product_url}</Link>
                      </td>
                      <td className="px-3 py-2 tabular-nums">{fmtPrice(p.current_price, p.currency)}</td>
                      <td className="px-3 py-2">{p.availability ?? "—"}</td>
                      <td className="px-3 py-2">{p.status}</td>
                      <td className="px-3 py-2 text-slate-600">{fmtDate(p.last_seen_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      <section>
        <h2 className="mb-2 font-medium">Checks</h2>
        {jobs.length === 0 ? (
          <p className="text-sm text-slate-500">No checks yet.</p>
        ) : (
          <ul className="space-y-3">
            {jobs.map((j) => (
              <li key={j.id} className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="font-medium">
                    {j.job_type === "products" ? "Product extraction" : "Verification"}: {j.status}
                  </span>
                  {j.result && j.job_type !== "products" && <StatusBadge status={j.result.status} />}
                  <span className="text-slate-500">attempts {j.attempts}</span>
                  <span className="text-slate-500">{fmtDate(j.completed_at ?? j.started_at ?? j.scheduled_at)}</span>
                </div>
                {j.error_message && <p className="mt-2 text-red-700">{j.error_message}</p>}
                {j.result && (
                  <details className="mt-2">
                    <summary className="cursor-pointer text-slate-600">{j.job_type === "products" ? "Summary" : "Evidence"}</summary>
                    <pre className="mt-2 max-h-96 overflow-auto rounded bg-slate-50 p-3 text-xs">
                      {JSON.stringify(j.result.evidence, null, 2)}
                    </pre>
                  </details>
                )}
                {j.result?.platform && (
                  <details className="mt-2">
                    <summary className="cursor-pointer text-slate-600">
                      Platform evidence: {PLATFORM_LABEL[j.result.platform.platform] ?? j.result.platform.platform}
                    </summary>
                    <pre className="mt-2 max-h-96 overflow-auto rounded bg-slate-50 p-3 text-xs">
                      {JSON.stringify(j.result.platform.evidence, null, 2)}
                    </pre>
                  </details>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
