import Link from "next/link";
import { redirect } from "next/navigation";
import { StatusBadge } from "@/components/StatusBadge";
import { apiFetch, type Domain, fmtDate, getJson, STATUS_LABEL } from "@/lib/api";

export const dynamic = "force-dynamic";

async function submit(formData: FormData) {
  "use server";
  const domain = String(formData.get("domain") ?? "");
  let error = "";
  try {
    const res = await apiFetch("/api/v1/discovery/domains", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ domain }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      error = typeof body.detail === "string" ? body.detail : `request failed (${res.status})`;
    }
  } catch {
    error = "API unreachable";
  }
  redirect(error ? `/discovery?error=${encodeURIComponent(error)}` : "/discovery");
}

type Page = { items: Domain[]; total: number; page: number; page_size: number };

export default async function Discovery({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; status?: string; page?: string }>;
}) {
  const { error, status, page = "1" } = await searchParams;
  const qs = new URLSearchParams({ page, page_size: "50", ...(status ? { status } : {}) });
  const data = await getJson<Page>(`/api/v1/domains?${qs}`);
  const pending = data?.items.some((d) => d.verification_status === "pending");
  const pageNum = Number(page) || 1;

  return (
    <div className="space-y-6">
      {pending && <meta httpEquiv="refresh" content="5" />}
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="mb-3 font-medium">Submit a domain</h2>
        <form action={submit} className="flex flex-col gap-2 sm:flex-row">
          <input
            name="domain"
            required
            placeholder="example-store.com"
            className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
            aria-label="Domain"
          />
          <button className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700">
            Verify
          </button>
        </form>
        {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
      </section>

      <section>
        <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
          <h2 className="mr-2 font-medium">Candidate domains {data ? `(${data.total.toLocaleString()})` : ""}</h2>
          <Link href="/discovery" className={!status ? "font-semibold underline" : "text-slate-600"}>All</Link>
          {Object.entries(STATUS_LABEL).map(([key, label]) => (
            <Link key={key} href={`/discovery?status=${key}`} className={status === key ? "font-semibold underline" : "text-slate-600"}>
              {label}
            </Link>
          ))}
        </div>

        {!data ? (
          <p className="text-sm text-red-700">API unreachable.</p>
        ) : data.items.length === 0 ? (
          <p className="text-sm text-slate-500">No candidate domains yet. Submit one above.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-200 text-slate-500">
                <tr>
                  <th className="px-3 py-2 font-medium">Domain</th>
                  <th className="px-3 py-2 font-medium">Verification</th>
                  <th className="px-3 py-2 font-medium">Confidence</th>
                  <th className="px-3 py-2 font-medium">First seen</th>
                  <th className="px-3 py-2 font-medium">Last checked</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((d) => (
                  <tr key={d.id} className="border-b border-slate-100 last:border-0">
                    <td className="px-3 py-2">
                      <Link href={`/domains/${d.id}`} className="text-slate-900 underline-offset-2 hover:underline">
                        {d.normalized_domain}
                      </Link>
                    </td>
                    <td className="px-3 py-2"><StatusBadge status={d.verification_status} /></td>
                    <td className="px-3 py-2 tabular-nums">
                      {d.verification_confidence == null ? "—" : `${Math.round(d.verification_confidence * 100)}%`}
                    </td>
                    <td className="px-3 py-2 text-slate-600">{fmtDate(d.first_seen_at)}</td>
                    <td className="px-3 py-2 text-slate-600">{fmtDate(d.last_checked_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && data.total > data.page_size && (
          <div className="mt-3 flex gap-4 text-sm">
            {pageNum > 1 && <Link href={`/discovery?${new URLSearchParams({ ...(status ? { status } : {}), page: String(pageNum - 1) })}`}>← Previous</Link>}
            {pageNum * data.page_size < data.total && (
              <Link href={`/discovery?${new URLSearchParams({ ...(status ? { status } : {}), page: String(pageNum + 1) })}`}>Next →</Link>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
