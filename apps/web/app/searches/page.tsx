import Link from "next/link";
import { redirect } from "next/navigation";
import { apiFetch, fmtDate, getJson } from "@/lib/api";

type Search = { id: string; name: string; filters: Record<string, string>; alerts_enabled: boolean; created_at: string };
type AlertRow = { id: string; store_id: string; domain: string; store_name: string | null; search: string; created_at: string; read_at: string | null };

async function remove(formData: FormData) {
  "use server";
  await apiFetch(`/api/v1/searches/${encodeURIComponent(String(formData.get("id")))}`, { method: "DELETE" });
  redirect("/searches");
}

async function markRead() {
  "use server";
  await apiFetch("/api/v1/alerts/read", { method: "POST" });
  redirect("/searches");
}

export default async function Searches() {
  const [searches, alerts] = await Promise.all([getJson<Search[]>("/api/v1/searches"), getJson<AlertRow[]>("/api/v1/alerts")]);
  const unread = (alerts ?? []).filter((a) => !a.read_at).length;

  return (
    <div className="space-y-6">
      <section>
        <h1 className="mb-2 text-lg font-semibold">Saved searches</h1>
        {!searches || searches.length === 0 ? (
          <p className="text-sm text-slate-500">None yet. Use “Save this search” on the Stores page.</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {searches.map((s) => (
              <li key={s.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2">
                <Link href={`/stores?${new URLSearchParams(s.filters)}`} className="font-medium hover:underline">{s.name}</Link>
                <span className="text-slate-500">
                  {Object.entries(s.filters).map(([k, v]) => `${k}=${v}`).join(", ") || "all stores"}
                </span>
                {s.alerts_enabled && <span className="rounded border border-sky-200 bg-sky-50 px-1.5 text-xs text-sky-800">alerts on</span>}
                <form action={remove} className="ml-auto">
                  <input type="hidden" name="id" value={s.id} />
                  <button className="text-slate-500 hover:text-red-700">Delete</button>
                </form>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <div className="mb-2 flex items-center gap-3">
          <h2 className="font-medium">Alerts {unread ? `(${unread} unread)` : ""}</h2>
          {unread > 0 && (
            <form action={markRead}>
              <button className="text-sm underline">Mark all read</button>
            </form>
          )}
        </div>
        <p className="mb-2 text-xs text-slate-500">New stores matching a saved search with alerts on (shown here; email delivery is not set up).</p>
        {!alerts || alerts.length === 0 ? (
          <p className="text-sm text-slate-500">No alerts yet.</p>
        ) : (
          <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white text-sm">
            {alerts.map((a) => (
              <li key={a.id} className={`flex flex-wrap justify-between gap-2 px-3 py-1.5 ${a.read_at ? "text-slate-500" : ""}`}>
                <span>
                  <Link href={`/stores/${a.store_id}`} className="font-medium hover:underline">{a.domain}</Link> · matches “{a.search}”
                </span>
                <span className="text-xs">{fmtDate(a.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
