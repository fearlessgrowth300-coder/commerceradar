// Server-side only: API_URL is the internal address (http://api:8000 in docker).
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

export const API_URL = process.env.API_URL ?? "http://localhost:8000";
export const SESSION_COOKIE = "cr_session";

/** fetch() against the API with the user's session forwarded as a Bearer token. */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  const headers: Record<string, string> = { ...((init.headers as Record<string, string>) ?? {}) };
  if (token) headers.authorization = `Bearer ${token}`;
  return fetch(`${API_URL}${path}`, { ...init, headers, cache: "no-store" });
}

export async function getJson<T>(path: string, opts: { redirectOn401?: boolean } = {}): Promise<T | null> {
  let res: Response;
  try {
    res = await apiFetch(path);
  } catch {
    return null;
  }
  if (res.status === 401 && opts.redirectOn401 !== false) redirect("/login"); // session expired
  return res.ok || res.status === 503 ? ((await res.json()) as T) : null;
}

export async function apiError(res: Response): Promise<string> {
  const body = await res.json().catch(() => ({}));
  return typeof body.detail === "string" ? body.detail : `request failed (${res.status})`;
}

export type Me = { id: string; email: string; name: string | null; is_admin: boolean; role: string; organization: { id: string; name: string; plan: string } };

export type Domain = {
  id: string;
  normalized_domain: string;
  verification_status: string;
  verification_confidence: number | null;
  current_resolution_status: string | null;
  first_seen_at: string;
  last_checked_at: string | null;
};

export type Job = {
  id: string;
  job_type: string;
  status: string;
  attempts: number;
  scheduled_at: string;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
  result: {
    status: string;
    confidence: number;
    resolution: string;
    evidence: Record<string, unknown>;
    platform?: { platform: string; confidence: number; evidence: Record<string, unknown> } | null;
  } | null;
};

export const STATUS_LABEL: Record<string, string> = {
  pending: "Pending",
  confirmed_ecommerce: "Confirmed ecommerce",
  probable_ecommerce: "Probable ecommerce",
  not_ecommerce: "Not ecommerce",
  uncertain: "Uncertain",
  temporarily_unreachable: "Unreachable",
  blocked: "Blocked (non-public)",
  inactive_storefront: "Inactive storefront",
};

export function fmtDate(s: string | null): string {
  return s ? new Date(s).toISOString().replace("T", " ").slice(0, 16) + " UTC" : "—";
}

export type Product = {
  id: string;
  product_url: string;
  title: string | null;
  image_url: string | null;
  sku: string | null;
  category: string | null;
  current_price: string | null; // decimal serialized as string, never a float
  currency: string | null;
  availability: string | null;
  status: string;
  extraction_method: string | null;
  first_seen_at: string;
  last_seen_at: string;
};

export function fmtPrice(price: string | null, currency: string | null): string {
  if (price == null) return "—";
  return currency ? `${price} ${currency}` : price;
}

export const PLATFORM_LABEL: Record<string, string> = {
  shopify: "Shopify",
  woocommerce: "WooCommerce",
  wix: "Wix",
  bigcommerce: "BigCommerce",
  magento: "Magento / Adobe Commerce",
  prestashop: "PrestaShop",
  squarespace: "Squarespace",
  opencart: "OpenCart",
  custom: "Custom (no known platform)",
  unknown: "Unknown (insufficient evidence)",
};
