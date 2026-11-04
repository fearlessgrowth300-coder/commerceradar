import { notFound, redirect } from "next/navigation";
import { apiError, apiFetch, fmtDate, getJson, type Me } from "@/lib/api";

type Overview = {
  organizations: { id: string; name: string; plan: string; subscription_status: string | null; members: number; exports_this_month: number; created_at: string }[];
  users: { id: string; email: string; name: string | null; is_admin: boolean; is_active: boolean; last_login_at: string | null; created_at: string }[];
  active_sessions: number;
  suppressed_domains: number;
};
type Suppression = { domain: string; reason: string | null; suppressed_at: string };

// Plain helper, not an action: only the fixed actions below can reach it.
async function call(path: string, method: string, body?: unknown) {
  const res = await apiFetch(path, {
    method,
    headers: body ? { "content-type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  }).catch(() => null);
  const error = !res ? "API unreachable" : res.ok ? "" : await apiError(res);
  redirect(error ? `/admin?error=${encodeURIComponent(error)}` : "/admin");
}

async function setPlan(f: FormData) {
  "use server";
  await call(`/api/v1/admin/organizations/${encodeURIComponent(String(f.get("id")))}/plan`, "PUT", { plan: String(f.get("plan")) });
}
async function toggleUser(f: FormData) {
  "use server";
  await call(`/api/v1/admin/users/${encodeURIComponent(String(f.get("id")))}`, "PATCH", { is_active: f.get("active") === "true" });
}
async function suppress(f: FormData) {
  "use server";
  await call("/api/v1/admin/suppressions", "POST", { domain: String(f.get("domain")), reason: String(f.get("reason") || "opt-out") });
}
async function unsuppress(f: FormData) {
  "use server";
  await call(`/api/v1/admin/suppressions/${encodeURIComponent(String(f.get("domain")))}`, "DELETE");
}

export default async function Admin({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const me = await getJson<Me>("/api/v1/auth/me");
  if (!me?.is_admin) notFound();
  const { error } = await searchParams;
  const [ov, sup] = await Promise.all([getJson<Overview>("/api/v1/admin/overview"), getJson<Suppression[]>("/api/v1/admin/suppressions")]);
  if (!ov) return <p className="text-sm text-red-700">API unreachable.</p>;

  return (
    <div className="space-y-6 text-sm">
      <h1 className="text-lg font-semibold">Admin</h1>
      {error && <p className="text-red-700">{error}</p>}
      <p className="text-slate-600">{ov.users.length} users · {ov.organizations.length} organizations · {ov.active_sessions} active sessions · {ov.suppressed_domains} suppressed domains</p>

      <section>
        <h2 className="mb-2 font-medium">Organizations</h2>
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-left">
            <thead className="border-b border-slate-200 text-slate-500">
              <tr><th className="px-3 py-2">Name</th><th className="px-3 py-2">Members</th><th className="px-3 py-2">Exports (month)</th><th className="px-3 py-2">Billing</th><th className="px-3 py-2">Plan</th></tr>
            </thead>
            <tbody>
              {ov.organizations.map((o) => (
                <tr key={o.id} className="border-b border-slate-100 last:border-0">
                  <td className="px-3 py-2">{o.name}</td>
                  <td className="px-3 py-2">{o.members}</td>
                  <td className="px-3 py-2">{o.exports_this_month}</td>
                  <td className="px-3 py-2">{o.subscription_status ?? "—"}</td>
                  <td className="px-3 py-2">
                    <form action={setPlan} className="flex gap-2">
                      <input type="hidden" name="id" value={o.id} />
                      <select name="plan" defaultValue={o.plan} aria-label={`Plan for ${o.name}`} className="rounded border border-slate-300 px-1">
                        <option value="free">Free</option><option value="pro">Pro</option><option value="business">Business</option>
                      </select>
                      <button className="underline">Save</button>
                    </form>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2 className="mb-2 font-medium">Users</h2>
        <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
          {ov.users.map((u) => (
            <li key={u.id} className="flex flex-wrap items-center gap-3 px-3 py-1.5">
              <span className={u.is_active ? "" : "text-slate-400 line-through"}>{u.email}</span>
              {u.is_admin && <span className="rounded border px-1 text-xs">admin</span>}
              <span className="text-xs text-slate-500">last login {fmtDate(u.last_login_at)}</span>
              {u.id !== me.id && (
                <form action={toggleUser} className="ml-auto">
                  <input type="hidden" name="id" value={u.id} />
                  <input type="hidden" name="active" value={String(!u.is_active)} />
                  <button className="underline">{u.is_active ? "Deactivate" : "Reactivate"}</button>
                </form>
              )}
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2 className="mb-2 font-medium">Opt-out / suppression list</h2>
        <p className="mb-2 text-slate-600">Suppressed domains are never crawled again and are hidden from the directory.</p>
        <form action={suppress} className="mb-3 flex flex-wrap gap-2">
          <input name="domain" required placeholder="example-store.com" aria-label="Domain to suppress" className="rounded-md border border-slate-300 px-2 py-1" />
          <input name="reason" placeholder="reason (e.g. owner request)" aria-label="Reason" className="rounded-md border border-slate-300 px-2 py-1" />
          <button className="rounded-md bg-slate-900 px-3 py-1 text-white">Suppress</button>
        </form>
        {!sup || sup.length === 0 ? (
          <p className="text-slate-500">No suppressed domains.</p>
        ) : (
          <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white">
            {sup.map((s) => (
              <li key={s.domain} className="flex flex-wrap items-center gap-3 px-3 py-1.5">
                <span className="font-medium">{s.domain}</span>
                <span className="text-slate-500">{s.reason}</span>
                <span className="text-xs text-slate-500">{fmtDate(s.suppressed_at)}</span>
                <form action={unsuppress} className="ml-auto">
                  <input type="hidden" name="domain" value={s.domain} />
                  <button className="underline">Remove</button>
                </form>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
