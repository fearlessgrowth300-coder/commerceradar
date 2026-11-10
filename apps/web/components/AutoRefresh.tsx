"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

// Re-fetches the current page's server data every `seconds` while it is mounted.
// Replaces <meta http-equiv="refresh">: after client-side navigation that tag kept firing and
// reloaded the page it was created on, pulling the user back (e.g. to /discovery).
export function AutoRefresh({ seconds }: { seconds: number }) {
  const router = useRouter();
  useEffect(() => {
    const t = setInterval(() => router.refresh(), seconds * 1000);
    return () => clearInterval(t);
  }, [router, seconds]);
  return null;
}
