type N = number | null | undefined;

export const nf = (v: N, d = 2) =>
  v === null || v === undefined || Number.isNaN(v)
    ? "—"
    : v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
export const usd = (v: N, d = 2) => (v === null || v === undefined ? "—" : `$${nf(v, d)}`);
export const pct = (v: N, d = 1, sign = false) =>
  v === null || v === undefined ? "—" : `${sign && v > 0 ? "+" : ""}${nf(v, d)}%`;
export const compact = (v: N) =>
  v === null || v === undefined
    ? "—"
    : Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(v);
export const tone = (v: N) => (v === null || v === undefined ? "" : v > 0 ? "text-bull" : v < 0 ? "text-bear" : "");
export const ago = (iso: string | null | undefined) => {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return `${Math.round(s)}s ago`;
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  if (s < 172800) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};
