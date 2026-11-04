import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = { title: "CommerceRadar" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-slate-50 text-slate-900 antialiased">
        <header className="border-b border-slate-200 bg-white">
          <nav className="mx-auto flex max-w-6xl items-center gap-5 px-4 py-3 text-sm">
            <Link href="/" className="font-semibold">CommerceRadar</Link>
            <Link href="/" className="text-slate-600 hover:text-slate-900">Dashboard</Link>
            <Link href="/stores" className="text-slate-600 hover:text-slate-900">Stores</Link>
            <Link href="/signals" className="text-slate-600 hover:text-slate-900">Signals</Link>
            <Link href="/discovery" className="text-slate-600 hover:text-slate-900">Discovery</Link>
            <Link href="/sources" className="text-slate-600 hover:text-slate-900">Sources</Link>
          </nav>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
