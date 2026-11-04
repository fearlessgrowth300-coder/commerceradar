import { redirect } from "next/navigation";
import { apiFetch, fmtDate, getJson } from "@/lib/api";

export const dynamic = "force-dynamic";

type Run = {
  id: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  records_found: number;
  records_processed: number;
  records_failed: number;
  error_summary: string | null;
  cursor: { log?: string[]; verify_queued?: number; done?: boolean } | null;
};
type Source = {
  id: string;
  name: string;
  source_type: string;
  enabled: boolean;
  urls_discovered: number;
  last_run: Run | null;
};

const ACTIVE = ["queued", "running", "retrying"];

// Plain helper (not an action): only the two fixed actions below can reach it.
async function post(path: string) {
  let error = "";
  try {
    const res = await apiFetch(path, { method: "POST" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      error = typeof body.detail === "string" ? body.detail : `request failed (${res.status})`;
    }
  } catch {
    error = "API unreachable";
  }
  redirect(error ? `/sources?error=${encodeURIComponent(error)}` : "/sources");
}

async function startRun(formData: FormData) {
  "use server";
  await post(`/api/v1/discovery/sources/${encodeURIComponent(String(formData.get("id")))}/runs`);
}

async function resumeRun(formData: FormData) {
  "use server";
  await post(`/api/v1/discovery/runs/${encodeURIComponent(String(formData.get("id")))}/resume`);
}

export default async function Sources({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const { error } = await searchParams;
  const [sources, runs] = await Promise.all([
    getJson<Source[]>("/api/v1/discovery/sources"),
    getJson<(Run & { source_id: string })[]>("/api/v1/discovery/runs?limit=30"),
  ]);
  const sourceName = Object.fromEntries((sources ?? []).map((s) => [s.id, s.name]));
  const duration = (r: Run) => {
    if (!r.started_at) return "—";
    const end = r.completed_at ? new Date(r.completed_at).getTime() : Date.now();
    const mins = Math.round((end - new Date(r.started_at).getTime()) / 60000);
    return mins < 60 ? `${mins} min` : `${(mins / 60).toFixed(1)} h`;
  };
  const anyActive = sources?.some((s) => s.last_run && ACTIVE.includes(s.last_run.status));

  return (
    <div className="space-y-4">
      {anyActive && <meta httpEquiv="refresh" content="10" />}
      <div>
        <h1 className="text-lg font-semibold">Discovery sources</h1>
        <p className="text-sm text-slate-600">
          Sources only add <em>candidate</em> domains. Every candidate goes through verification before it can become a store.
        </p>
      </div>
      {error && <p className="text-sm text-red-700">{error}</p>}
      {!sources ? (
        <p className="text-sm text-red-700">API unreachable.</p>
      ) : (
        <ul className="space-y-3">
          {sources.map((s) => {
            const run = s.last_run;
            const active = run && ACTIVE.includes(run.status);
            const resumable = run && ["failed", "cancelled"].includes(run.status) && !run.cursor?.done;
            return (
              <li key={s.id} className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="font-medium">{s.name}</span>
                  <span className="text-slate-500">{s.source_type}</span>
                  <span className="text-slate-500">{s.urls_discovered.toLocaleString()} URLs found so far</span>
                  <div className="ml-auto flex gap-2">
                    {resumable && (
                      <form action={resumeRun}>
                        <input type="hidden" name="id" value={run.id} />
                        <button className="rounded-md border border-slate-300 px-3 py-1">Resume</button>
                      </form>
                    )}
                    <form action={startRun}>
                      <input type="hidden" name="id" value={s.id} />
                      <button
                        disabled={!!active || !s.enabled}
                        className="rounded-md bg-slate-900 px-3 py-1 text-white disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        {active ? "Running…" : "Run now"}
                      </button>
                    </form>
                  </div>
                </div>
                {run ? (
                  <div className="mt-2 text-slate-600">
                    <p>
                      Last run: <span className="font-medium">{run.status}</span> · started {fmtDate(run.started_at)} · found{" "}
                      {run.records_found.toLocaleString()} · stored {run.records_processed.toLocaleString()} · invalid{" "}
                      {run.records_failed.toLocaleString()} · queued for verification {run.cursor?.verify_queued ?? 0}
                    </p>
                    {run.error_summary && <p className="mt-1 text-red-700">{run.error_summary}</p>}
                    {!!run.cursor?.log?.length && (
                      <details className="mt-1">
                        <summary className="cursor-pointer">Batch log</summary>
                        <pre className="mt-1 max-h-64 overflow-auto rounded bg-slate-50 p-2 text-xs">{run.cursor.log.join("\n")}</pre>
                      </details>
                    )}
                  </div>
                ) : (
                  <p className="mt-2 text-slate-500">Never run.</p>
                )}
              </li>
            );
          })}
        </ul>
      )}

      <section>
        <h2 className="mb-2 font-medium">Discovery history</h2>
        {!runs || runs.length === 0 ? (
          <p className="text-sm text-slate-500">No runs yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-200 text-slate-500">
                <tr>
                  <th className="px-3 py-2 font-medium">Source</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Started</th>
                  <th className="px-3 py-2 font-medium">Duration</th>
                  <th className="px-3 py-2 font-medium">Found</th>
                  <th className="px-3 py-2 font-medium">Stored</th>
                  <th className="px-3 py-2 font-medium">Invalid</th>
                  <th className="px-3 py-2 font-medium">Queued for verification</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id} className="border-b border-slate-100 last:border-0">
                    <td className="px-3 py-2">{sourceName[r.source_id] ?? "—"}</td>
                    <td className="px-3 py-2">{r.status}</td>
                    <td className="px-3 py-2 text-slate-600">{fmtDate(r.started_at)}</td>
                    <td className="px-3 py-2">{duration(r)}</td>
                    <td className="px-3 py-2 tabular-nums">{r.records_found.toLocaleString()}</td>
                    <td className="px-3 py-2 tabular-nums">{r.records_processed.toLocaleString()}</td>
                    <td className="px-3 py-2 tabular-nums">{r.records_failed.toLocaleString()}</td>
                    <td className="px-3 py-2 tabular-nums">{r.cursor?.verify_queued ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
