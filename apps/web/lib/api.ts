// Server-side only: API_URL is the internal address (http://api:8000 in docker).
export const API_URL = process.env.API_URL ?? "http://localhost:8000";

export async function getJson<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_URL}${path}`, { cache: "no-store" });
    return res.ok || res.status === 503 ? ((await res.json()) as T) : null;
  } catch {
    return null;
  }
}

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
  result: { status: string; confidence: number; resolution: string; evidence: Record<string, unknown> } | null;
};

export const STATUS_LABEL: Record<string, string> = {
  pending: "Pending",
  confirmed_ecommerce: "Confirmed ecommerce",
  probable_ecommerce: "Probable ecommerce",
  not_ecommerce: "Not ecommerce",
  uncertain: "Uncertain",
  temporarily_unreachable: "Unreachable",
  blocked: "Blocked (non-public)",
};

export function fmtDate(s: string | null): string {
  return s ? new Date(s).toISOString().replace("T", " ").slice(0, 16) + " UTC" : "—";
}
