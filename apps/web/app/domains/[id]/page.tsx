import Link from "next/link";
import { notFound } from "next/navigation";
import { StatusBadge } from "@/components/StatusBadge";
import { type Domain, fmtDate, getJson, type Job, PLATFORM_LABEL } from "@/lib/api";

export const dynamic = "force-dynamic";

type Detail = {
  domain: Domain;
  jobs: Job[];
  store_id: string | null;
  platform: string | null;
  platform_confidence: number | null;
};

export default async function DomainPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const data = await getJson<Detail>(`/api/v1/domains/${encodeURIComponent(id)}`);
  if (!data) notFound();
  const { domain, jobs, store_id, platform, platform_confidence } = data;
  const inFlight = jobs.some((j) => ["queued", "running", "retrying"].includes(j.status));

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
      </section>

      <section>
        <h2 className="mb-2 font-medium">Verification checks</h2>
        {jobs.length === 0 ? (
          <p className="text-sm text-slate-500">No checks yet.</p>
        ) : (
          <ul className="space-y-3">
            {jobs.map((j) => (
              <li key={j.id} className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="font-medium">{j.status}</span>
                  {j.result && <StatusBadge status={j.result.status} />}
                  <span className="text-slate-500">attempts {j.attempts}</span>
                  <span className="text-slate-500">{fmtDate(j.completed_at ?? j.started_at ?? j.scheduled_at)}</span>
                </div>
                {j.error_message && <p className="mt-2 text-red-700">{j.error_message}</p>}
                {j.result && (
                  <details className="mt-2">
                    <summary className="cursor-pointer text-slate-600">Evidence</summary>
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
