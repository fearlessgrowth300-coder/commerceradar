import Link from "next/link";
import { authenticate } from "@/lib/auth";

async function login(formData: FormData) {
  "use server";
  await authenticate("login", formData);
}

export default async function Login({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const { error } = await searchParams;
  return (
    <div className="mx-auto max-w-sm space-y-4">
      <h1 className="text-lg font-semibold">Sign in</h1>
      <form action={login} className="space-y-3 rounded-lg border border-slate-200 bg-white p-4 text-sm">
        <label className="block">
          Email
          <input name="email" type="email" required autoComplete="email" className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2" />
        </label>
        <label className="block">
          Password
          <input name="password" type="password" required autoComplete="current-password" className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2" />
        </label>
        {error && <p className="text-red-700">{error}</p>}
        <button className="w-full rounded-md bg-slate-900 px-4 py-2 font-medium text-white">Sign in</button>
      </form>
      <p className="text-sm text-slate-600">
        No account? <Link href="/register" className="underline">Create one</Link>
      </p>
    </div>
  );
}
