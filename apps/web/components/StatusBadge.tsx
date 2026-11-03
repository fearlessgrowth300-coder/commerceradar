import { STATUS_LABEL } from "@/lib/api";

const TONE: Record<string, string> = {
  confirmed_ecommerce: "border-emerald-200 bg-emerald-50 text-emerald-800",
  probable_ecommerce: "border-sky-200 bg-sky-50 text-sky-800",
  not_ecommerce: "border-slate-200 bg-slate-100 text-slate-700",
  blocked: "border-red-200 bg-red-50 text-red-800",
  temporarily_unreachable: "border-amber-200 bg-amber-50 text-amber-800",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`inline-block rounded-md border px-2 py-0.5 text-xs ${TONE[status] ?? "border-slate-200 bg-white text-slate-700"}`}>
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}
