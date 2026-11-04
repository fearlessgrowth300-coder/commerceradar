import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { apiError, apiFetch, getJson, type Me, SESSION_COOKIE } from "@/lib/api";

type Plan = { label: string; saved_searches: number; alerts: boolean; exports_per_month: number; export_max_rows: number };
type Account = {
  plan: string;
  limits: Plan;
  usage: { exports: number; export_rows: number; saved_searches: number };
  billing_configured: boolean;
  subscription_status: string | null;
  plans: Record<string, Plan>;
};

async function logout() {
  "use server";
  await apiFetch("/api/v1/auth/logout", { method: "POST" }).catch(() => null);
  (await cookies()).delete(SESSION_COOKIE);
  redirect("/login");
}

async function upgrade(formData: FormData) {
  "use server";
  let target = "/account?error=API%20unreachable";
  try {
    const res = await apiFetch("/api/v1/billing/checkout", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ plan: String(formData.get("plan")) }),
    });
    target = res.ok ? (await res.json()).url : `/account?error=${encodeURIComponent(await apiError(res))}`;
  } catch {}
  redirect(target);
}

export default async function AccountPage({ searchParams }: { searchParams: Promise<{ error?: string; billing?: string }> }) {
  const { error, billing } = await searchParams;
  const [me, acct] = await Promise.all([getJson<Me>("/api/v1/auth/me"), getJson<Account>("/api/v1/account")]);
  if (!me || !acct) return <p className="text-sm text-red-700">API unreachable.</p>;

  return (
    <div className="space-y-6">
      <section className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
        <h1 className="text-lg font-semibold">Account</h1>
        <p className="mt-1 text-slate-600">
          {me.email} · {me.organization.name} ({me.role}){me.is_admin && " · platform admin"}
        </p>
        <form action={logout} className="mt-3">
          <button className="rounded-md border border-slate-300 px-3 py-1">Sign out</button>
        </form>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 text-sm">
        <h2 className="font-medium">Plan: {acct.limits.label}</h2>
        <ul className="mt-2 space-y-1 text-slate-700">
          <li>Saved searches: {acct.usage.saved_searches} / {acct.limits.saved_searches}</li>
          <li>Exports this month: {acct.usage.exports} / {acct.limits.exports_per_month} (up to {acct.limits.export_max_rows.toLocaleString()} rows each)</li>
          <li>Alerts: {acct.limits.alerts ? "included" : "not included"}</li>
        </ul>
        {billing === "success" && <p className="mt-2 text-emerald-700">Payment received. Your plan updates once Stripe confirms it.</p>}
        {error && <p className="mt-2 text-red-700">{error}</p>}
        {!acct.billing_configured && (
          <p className="mt-2 text-slate-500">Online payment is not set up yet. Ask an administrator to change your plan.</p>
        )}
        {acct.billing_configured && me.role === "owner" && (
          <div className="mt-3 flex gap-2">
            {["pro", "business"].filter((p) => p !== acct.plan).map((p) => (
              <form key={p} action={upgrade}>
                <input type="hidden" name="plan" value={p} />
                <button className="rounded-md bg-slate-900 px-3 py-1 text-white">Upgrade to {acct.plans[p].label}</button>
              </form>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
