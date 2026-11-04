import { notFound, redirect } from "next/navigation";
import { apiError, apiFetch, getJson, type Me } from "@/lib/api";

type Next = { item: { domain: string; url: string } | null; progress: Record<string, number>; platforms: string[] };

async function label(formData: FormData) {
  "use server";
  const res = await apiFetch("/api/v1/admin/benchmark/label", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      domain: String(formData.get("domain")),
      verdict: String(formData.get("verdict")),
      platform: String(formData.get("platform") || "") || null,
      notes: String(formData.get("notes") || ""),
    }),
  }).catch(() => null);
  const error = !res ? "API unreachable" : res.ok ? "" : await apiError(res);
  redirect(error ? `/admin/benchmark?error=${encodeURIComponent(error)}` : "/admin/benchmark");
}

export default async function Benchmark({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const me = await getJson<Me>("/api/v1/auth/me");
  if (!me?.is_admin) notFound();
  const { error } = await searchParams;
  const data = await getJson<Next>("/api/v1/admin/benchmark/next");
  if (!data) return <p className="text-sm text-red-700">API unreachable.</p>;
  const p = data.progress;
  const done = (p.labelled ?? 0) + (p.cannot_tell ?? 0);
  const total = done + (p.open ?? 0);

  return (
    <div className="mx-auto max-w-xl space-y-4 text-sm">
      <h1 className="text-lg font-semibold">Accuracy benchmark: blind labelling</h1>
      <p className="text-slate-600">
        Open the site, look at it as a shopper would, and answer: <b>can a visitor pick a product and start a purchase here?</b>{" "}
        Catalogue-only, &ldquo;contact us to buy&rdquo; and marketplace seller pages are <b>not a store</b>. CommerceRadar&rsquo;s own
        verdict is hidden on purpose.
      </p>
      <ul className="list-disc space-y-1 pl-5 text-slate-600">
        <li><b>Store</b>: you could add a product to a cart / start checkout (no need to buy).</li>
        <li><b>Closed / password page</b>: a shop exists but shows &ldquo;opening soon&rdquo;, a password form, or &ldquo;store unavailable&rdquo;.</li>
        <li><b>Didn&rsquo;t load / blocked</b>: error page, security check you&rsquo;d have to pass, login wall. Don&rsquo;t try to get around it.</li>
        <li><b>Platform</b>: only if the site shows it (e.g. &ldquo;Powered by Shopify&rdquo;, a Shopify/Woo checkout). Otherwise leave &ldquo;Don&rsquo;t know&rdquo;.</li>
        <li>Spend under a minute per site. Your email and the time are saved with each answer.</li>
      </ul>
      <p className="text-slate-500">{done} of {total} done{p.cannot_tell ? ` (${p.cannot_tell} could not be judged)` : ""}</p>
      {error && <p className="text-red-700">{error}</p>}
      {!data.item ? (
        <p className="rounded-lg border border-slate-200 bg-white p-4">Queue empty. Results: <code>GET /api/v1/admin/benchmark/report</code></p>
      ) : (
        <form action={label} className="space-y-3 rounded-lg border border-slate-200 bg-white p-4">
          <input type="hidden" name="domain" value={data.item.domain} />
          <a href={data.item.url} target="_blank" rel="noopener noreferrer nofollow" className="block break-all text-base font-medium underline">
            {data.item.domain} ↗
          </a>
          <label className="block">
            Platform, if you can tell (optional)
            <select name="platform" defaultValue="" className="mt-1 block w-full rounded-md border border-slate-300 px-2 py-1.5">
              <option value="">Don&rsquo;t know</option>
              {data.platforms.map((pl) => (
                <option key={pl} value={pl}>{pl}</option>
              ))}
            </select>
          </label>
          <textarea name="notes" placeholder="Notes (optional)" aria-label="Notes" className="block w-full rounded-md border border-slate-300 px-2 py-1.5" />
          <div className="flex flex-wrap gap-2">
            <button name="verdict" value="store" className="rounded-md bg-emerald-700 px-3 py-1.5 text-white">Store (can buy)</button>
            <button name="verdict" value="not_store" className="rounded-md bg-slate-800 px-3 py-1.5 text-white">Not a store</button>
            <button name="verdict" value="inactive_storefront" className="rounded-md border border-slate-300 px-3 py-1.5">Closed / password page</button>
            <button name="verdict" value="inaccessible" className="rounded-md border border-slate-300 px-3 py-1.5">Didn&rsquo;t load / blocked</button>
            <button name="verdict" value="uncertain" className="rounded-md border border-slate-300 px-3 py-1.5">Loaded, can&rsquo;t tell</button>
          </div>
        </form>
      )}
    </div>
  );
}
