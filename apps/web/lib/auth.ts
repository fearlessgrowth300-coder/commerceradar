"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { API_URL, apiError, PROXY_HEADERS, SESSION_COOKIE } from "@/lib/api";

export async function authenticate(kind: "login" | "register", formData: FormData) {
  if (kind !== "login" && kind !== "register") throw new Error("invalid action");
  const body = Object.fromEntries(
    ["email", "password", "name", "organization"].map((k) => [k, String(formData.get(k) ?? "") || undefined]),
  );
  let error = "";
  try {
    const res = await fetch(`${API_URL}/api/v1/auth/${kind}`, {
      method: "POST",
      headers: { "content-type": "application/json", ...PROXY_HEADERS },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    if (res.ok) {
      (await cookies()).set(SESSION_COOKIE, (await res.json()).token, {
        httpOnly: true,
        sameSite: "lax",
        secure: process.env.COOKIE_SECURE === "true", // set true once served over HTTPS
        path: "/",
        maxAge: 30 * 86400,
      });
    } else {
      error = await apiError(res);
    }
  } catch {
    error = "API unreachable";
  }
  redirect(error ? `/${kind}?error=${encodeURIComponent(error)}` : "/");
}
