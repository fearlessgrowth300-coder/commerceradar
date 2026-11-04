import Link from "next/link";
import { authenticate } from "@/lib/auth";

async function register(formData: FormData) {
  "use server";
  await authenticate("register", formData);
}

export default async function Register({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const { error } = await searchParams;
  return (
    <div className="mx-auto max-w-sm space-y-4">
      <h1 className="text-lg font-semibold">Create an account</h1>
      <form action={register} className="space-y-3 rounded-lg border border-slate-200 bg-white p-4 text-sm">
        <label className="block">
          Name
          <input name="name" autoComplete="name" className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2" />
        </label>
        <label className="block">
          Organization
          <input name="organization" autoComplete="organization" className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2" />
        </label>
        <label className="block">
          Email
          <input name="email" type="email" required autoComplete="email" className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2" />
        </label>
        <label className="block">
          Password <span className="text-slate-500">(10+ characters)</span>
          <input name="password" type="password" required minLength={10} autoComplete="new-password" className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2" />
        </label>
        {error && <p className="text-red-700">{error}</p>}
        <button className="w-full rounded-md bg-slate-900 px-4 py-2 font-medium text-white">Create account</button>
      </form>
      <p className="text-sm text-slate-600">
        Already registered? <Link href="/login" className="underline">Sign in</Link>
      </p>
    </div>
  );
}
