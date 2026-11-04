import type { Metadata } from "next";
import Link from "next/link";
import { getJson, type Me } from "@/lib/api";
import "./globals.css";

export const metadata: Metadata = { title: "CommerceRadar" };

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const me = await getJson<Me>("/api/v1/auth/me", { redirectOn401: false });
  return (
    <html lang="en">
      <body className="min-h-screen bg-slate-50 text-slate-900 antialiased">
        <header className="border-b border-slate-200 bg-white">
          <nav className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3 text-sm">
            <Link href="/" className="font-semibold">CommerceRadar</Link>
            {me && (
              <>
                <Link href="/" className="text-slate-600 hover:text-slate-900">Dashboard</Link>
                <Link href="/stores" className="text-slate-600 hover:text-slate-900">Stores</Link>
                <Link href="/signals" className="text-slate-600 hover:text-slate-900">Signals</Link>
                <Link href="/searches" className="text-slate-600 hover:text-slate-900">Saved &amp; alerts</Link>
                <Link href="/discovery" className="text-slate-600 hover:text-slate-900">Discovery</Link>
                <Link href="/sources" className="text-slate-600 hover:text-slate-900">Sources</Link>
                {me.is_admin && <Link href="/admin" className="text-slate-600 hover:text-slate-900">Admin</Link>}
                <Link href="/account" className="ml-auto text-slate-600 hover:text-slate-900">{me.email}</Link>
              </>
            )}
          </nav>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
