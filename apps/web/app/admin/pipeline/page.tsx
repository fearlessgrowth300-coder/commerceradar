import { AutoRefresh } from "@/components/AutoRefresh";
import { notFound } from "next/navigation";
import { fmtDate, getJson, type Me } from "@/lib/api";

type Scan = { offset?: number; total?: number; matched?: number; new_domains?: number; done?: boolean; error?: string | null; at?: string;
  sampled?: number; resolved?: number } | null;
type Pipeline = {
  measured_at: string;
  domains_by_status: Record<string, number>;
  stores_by_activity: Record<string, number>;
  stores_total: number;
  new_stores_1h: number;
  new_stores_24h: number;
  new_stores_hourly: number[];
  checks: { completed?: number; succeeded?: number; failed?: number; per_hour?: number; avg_seconds?: number | null };
  queues: Record<string, number>;
  scans: { full: Scan; sample: Scan };
  server: { load: number[] | null; cpus: number | null };
  recent_stores: { id: string; name: string | null; url: string | null; platform: string; activity: string; first_verified_at: string | null }[];
};

const n = (v?: number | null) => (v == null ? "—" : v.toLocaleString("en-US"));

function Card({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded border border-slate-200 bg-white p-4">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {note && <div className="mt-1 text-xs text-slate-500">{note}</div>}
    </div>
  );
}

function Counts({ title, rows }: { title: string; rows: Record<string, number> }) {
  const list = Object.entries(rows).sort((a, b) => b[1] - a[1]);
  return (
    <section className="rounded border border-slate-200 bg-white p-4">
      <h2 className="mb-2 font-semibold">{title}</h2>
      <table className="w-full text-sm">
        <tbody>
          {list.map(([k, v]) => (
            <tr key={k} className="border-t border-slate-100">
              <td className="py-1 text-slate-600">{k.replaceAll("_", " ")}</td>
              <td className="py-1 text-right tabular-nums">{n(v)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export default async function PipelinePage() {
  const me = await getJson<Me>("/api/v1/auth/me");
  if (!me?.is_admin) notFound();
  const p = await getJson<Pipeline>("/api/v1/admin/pipeline");
  if (!p) return <p className="text-sm text-red-700">Pipeline data unavailable (API unreachable).</p>;

  const full = p.scans.full;
  const pct = full?.total ? (100 * (full.offset ?? 0)) / full.total : 0;
  const max = Math.max(1, ...p.new_stores_hourly);
  const [load1] = p.server.load ?? [];
  const busy = load1 != null && p.server.cpus ? Math.min(100, Math.round((100 * load1) / p.server.cpus)) : null;

  return (
    <div className="space-y-6">
      {/* refresh every 30 s while the page is open */}
      <AutoRefresh seconds={30} />
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold">Pipeline</h1>
        <span className="text-xs text-slate-500">updated {fmtDate(p.measured_at)} · refreshes every 30 s</span>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Card label="Live stores" value={n(p.stores_total)} note="verified, not merged" />
        <Card label="New stores (1 h)" value={n(p.new_stores_1h)} note={`${n(p.new_stores_24h)} in 24 h`} />
        <Card label="Checks (last hour)" value={n(p.checks.completed)} note={`${n(p.checks.failed)} failed`} />
        <Card label="Waiting to be checked" value={n(p.domains_by_status.pending)} note={`queue: ${n(p.queues.verification)} · products: ${n(p.queues.products)}`} />
      </div>

      <section className="rounded border border-slate-200 bg-white p-4">
        <h2 className="font-semibold">Full zone scan (.shop + .store)</h2>
        {full ? (
          <>
            <div className="mt-3 h-3 w-full overflow-hidden rounded bg-slate-100">
              <div className="h-full bg-emerald-500" style={{ width: `${pct.toFixed(2)}%` }} />
            </div>
            <p className="mt-2 text-sm text-slate-600">
              {n(full.offset)} of {n(full.total)} names ({pct.toFixed(1)}%) · {n(full.matched)} point to Shopify ·{" "}
              {n(full.new_domains)} new domains queued for checking
              {full.at && <> · last batch {fmtDate(full.at)}</>}
            </p>
            {full.done && <p className="mt-1 text-sm text-emerald-700">Scan finished.</p>}
            {full.error && <p className="mt-1 text-sm text-red-700">Scan stopped with an error — see the server log.</p>}
            <p className="mt-1 text-xs text-slate-500">A Shopify DNS match is not a store until it passes the check.</p>
          </>
        ) : (
          <p className="mt-2 text-sm text-slate-500">No scan running.</p>
        )}
      </section>

      <section className="rounded border border-slate-200 bg-white p-4">
        <h2 className="mb-3 font-semibold">New verified stores per hour (last 24 h)</h2>
        <div className="flex h-32 items-end gap-1">
          {p.new_stores_hourly.map((v, i) => (
            <div key={i} className="flex-1 bg-sky-500" style={{ height: `${(100 * v) / max}%` }} title={`${23 - i} h ago: ${v}`} />
          ))}
        </div>
        <div className="mt-1 flex justify-between text-xs text-slate-500"><span>24 h ago</span><span>now</span></div>
      </section>

      <div className="grid gap-4 md:grid-cols-3">
        <Counts title="Stores by activity" rows={p.stores_by_activity} />
        <Counts title="Domains by check result" rows={p.domains_by_status} />
        <section className="rounded border border-slate-200 bg-white p-4">
          <h2 className="mb-2 font-semibold">Server</h2>
          <p className="text-sm text-slate-600">Load: {p.server.load?.join(" / ") ?? "—"} (1 / 5 / 15 min) on {p.server.cpus ?? "?"} CPUs</p>
          {busy != null && (
            <p className={`mt-1 text-sm ${busy > 70 ? "text-red-700" : "text-slate-600"}`}>≈{busy}% busy (1-min load ÷ CPUs)</p>
          )}
          <p className="mt-2 text-sm text-slate-600">Queues: {Object.entries(p.queues).map(([k, v]) => `${k} ${v}`).join(" · ")}</p>
        </section>
      </div>

      <section className="rounded border border-slate-200 bg-white p-4">
        <h2 className="mb-2 font-semibold">Latest verified stores</h2>
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase text-slate-500">
            <tr><th className="py-1">Store</th><th>Platform</th><th>Activity</th><th className="text-right">Verified</th></tr>
          </thead>
          <tbody>
            {p.recent_stores.map((s) => (
              <tr key={s.id} className="border-t border-slate-100">
                <td className="py-1">
                  <a href={`/stores/${s.id}`} className="underline">{s.name || s.url || s.id}</a>
                  {s.url && <span className="ml-2 text-xs text-slate-500">{s.url.replace(/^https?:\/\//, "")}</span>}
                </td>
                <td>{s.platform}</td>
                <td>{s.activity.replaceAll("_", " ")}</td>
                <td className="text-right text-slate-500">{s.first_verified_at ? fmtDate(s.first_verified_at) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
