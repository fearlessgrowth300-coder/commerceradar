// Server component: talks to the API (never the DB). API_URL is the internal address (http://api:8000 in docker).
import { API_URL, getJson } from "@/lib/api";

export const dynamic = "force-dynamic";

type Health = { status: string; checks: Record<string, string> };

type Overview = {
  candidate_domains: number;
  verified_stores: number;
  active_stores: number;
  inactive_stores: number;
  discovery_runs: number;
  failed_jobs: number;
};

const LABELS: [keyof Overview, string][] = [
  ["candidate_domains", "Candidate domains"],
  ["verified_stores", "Verified stores"],
  ["active_stores", "Active stores"],
  ["inactive_stores", "Inactive stores"],
  ["discovery_runs", "Discovery runs"],
  ["failed_jobs", "Failed jobs"],
];

export default async function Dashboard() {
  const [health, overview] = await Promise.all([
    getJson<Health>("/health"),
    getJson<Overview>("/api/v1/dashboard/overview"),
  ]);

  return (
    <div className="space-y-6">
      <section>
        <h2 className="mb-2 text-sm font-medium text-slate-500">System status</h2>
        {health ? (
          <div className="flex flex-wrap gap-2">
            {Object.entries(health.checks).map(([name, value]) => (
              <span
                key={name}
                className={`rounded-md border px-2 py-1 text-sm ${
                  value === "ok" ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-red-200 bg-red-50 text-red-800"
                }`}
              >
                {name}: {value}
              </span>
            ))}
          </div>
        ) : (
          <p className="text-sm text-red-700">API unreachable at {API_URL}</p>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-sm font-medium text-slate-500">Overview</h2>
        {overview ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {LABELS.map(([key, label]) => (
              <div key={key} className="rounded-lg border border-slate-200 bg-white p-4">
                <div className="text-sm text-slate-500">{label}</div>
                <div className="mt-1 text-2xl font-semibold tabular-nums">{overview[key].toLocaleString()}</div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-slate-500">No data available.</p>
        )}
        {overview && overview.candidate_domains === 0 && (
          <p className="mt-3 text-sm text-slate-500">No domains yet. Submit one on the Discovery page.</p>
        )}
      </section>
    </div>
  );
}
