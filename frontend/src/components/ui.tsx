"use client";
import Link from "next/link";
import { ReactNode, useState } from "react";
import { earningsLabel, usePane } from "@/lib/pane-context";

export function Card({ title, right, children, className = "" }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`panel min-w-0 p-4 ${className}`}>
      {(title || right) && (
        <header className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold tracking-wide text-ink">{title}</h2>
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

const TONES: Record<string, string> = {
  bull: "bg-bull/15 text-bull border-bull/30",
  bear: "bg-bear/15 text-bear border-bear/30",
  warn: "bg-warn/15 text-warn border-warn/30",
  info: "bg-accent/15 text-accent border-accent/30",
  muted: "bg-panel-2 text-muted border-line",
};

export function Badge({ tone = "muted", children }: { tone?: keyof typeof TONES; children: ReactNode }) {
  return <span className={`inline-flex items-center rounded-md border px-1.5 py-0.5 text-[11px] font-medium ${TONES[tone]}`}>{children}</span>;
}

/** Shariah status is shown only as a colour: green = compliant, red = non-compliant, default = unknown. */
export function shariahTone(status?: string | null) {
  return status === "Compliant" ? "text-bull" : status === "Non-Compliant" ? "text-bear" : "text-accent";
}

/** Small amber "E" next to a ticker that reports earnings within the next 7 days. */
export function EarningsMarker({ symbol }: { symbol: string }) {
  const m = usePane()?.earnings[symbol];
  if (!m) return null;
  return (
    <span title={earningsLabel(m)} aria-label={earningsLabel(m)}
      className="ml-1 inline-flex h-4 min-w-4 items-center justify-center rounded border border-warn/50 bg-warn/15 px-0.5 align-middle text-[9px] font-bold text-warn">
      E
    </span>
  );
}

/** Ticker coloured by Shariah status. Clicking opens the stock in the right-hand panel
 *  (ctrl/cmd-click opens the full analysis page). */
export function Ticker({ symbol, status, link = true, className = "" }: { symbol: string; status?: string | null; link?: boolean; className?: string }) {
  const pane = usePane();
  const title = status === "Compliant" ? "Shariah-compliant" : status === "Non-Compliant" ? "Not Shariah-compliant" : "Shariah status not available";
  const cls = `font-semibold ${shariahTone(status)} ${className}`;
  const active = pane?.selected === symbol.toUpperCase();
  if (!link) return <span className="whitespace-nowrap"><span title={title} className={cls}>{symbol}</span><EarningsMarker symbol={symbol} /></span>;
  return (
    <span className="whitespace-nowrap">
      <Link href={`/stock/${symbol}`} title={`${title} — click for details`}
        className={`${cls} hover:underline ${active ? "rounded bg-panel-2 px-1 underline" : ""}`}
        onClick={(e) => {
          if (!pane || e.metaKey || e.ctrlKey || e.shiftKey) return;
          e.preventDefault();
          pane.select(symbol);
        }}>
        {symbol}
      </Link>
      <EarningsMarker symbol={symbol} />
    </span>
  );
}

export function ShariahLegend() {
  return (
    <span className="text-xs text-muted">
      <span className="font-semibold text-bull">Green</span> = Shariah-compliant · <span className="font-semibold text-bear">red</span> = non-compliant ·{" "}
      <span className="font-semibold text-accent">blue</span> = not yet screened ·{" "}
      <span className="rounded border border-warn/50 bg-warn/15 px-0.5 text-[9px] font-bold text-warn">E</span> = earnings within 7 days
    </span>
  );
}

export function ShariahBadge({ status }: { status?: string | null }) {
  const t = status === "Compliant" ? "bull" : status === "Non-Compliant" ? "bear" : "warn";
  return <Badge tone={t}>{status ?? "Unknown"}</Badge>;
}

export function OutcomeBadge({ outcome }: { outcome?: string }) {
  const t = outcome === "pass" ? "bull" : outcome === "fail" ? "bear" : "warn";
  return <Badge tone={t}>{outcome === "missing" ? "missing data" : outcome ?? "—"}</Badge>;
}

export function Stat({ label, value, sub, className = "" }: { label: ReactNode; value: ReactNode; sub?: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-line bg-panel-2 p-3 ${className}`}>
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className="num mt-1 text-lg">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  );
}

/** Click-to-explain: shows how a metric was calculated. */
export function Info({ children, label = "How is this calculated?" }: { children: ReactNode; label?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-block">
      <button type="button" aria-label={label} onClick={() => setOpen(!open)}
        className="ml-1 inline-flex h-4 w-4 items-center justify-center rounded-full border border-line text-[10px] text-muted hover:text-accent">
        ?
      </button>
      {open && (
        <span role="dialog" className="absolute left-0 top-5 z-30 w-80 whitespace-normal rounded-lg border border-line bg-panel-2 p-3 text-xs font-normal normal-case leading-relaxed tracking-normal text-ink shadow-xl">
          {children}
          <button type="button" className="mt-2 block text-accent" onClick={() => setOpen(false)}>Close</button>
        </span>
      )}
    </span>
  );
}

export function ErrorBox({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="rounded-lg border border-bear/40 bg-bear/10 p-3 text-sm text-bear">{error}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-line p-6 text-center text-sm text-muted">{children}</div>;
}

export function Button({ children, variant = "primary", ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" | "danger" }) {
  const v = variant === "primary" ? "bg-accent text-bg hover:bg-accent/90" : variant === "danger" ? "bg-bear/20 text-bear hover:bg-bear/30" : "border border-line hover:bg-panel-2";
  return <button {...p} className={`rounded-lg px-3 py-1.5 text-sm font-medium disabled:opacity-50 ${v} ${p.className ?? ""}`}>{children}</button>;
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="flex flex-col gap-1 text-xs text-muted">
      <span>{label}</span>
      {children}
      {hint && <span className="text-[11px] text-muted/80">{hint}</span>}
    </label>
  );
}

export function useSort<T>(rows: T[], initial: keyof T | null = null, desc = true) {
  const [key, setKey] = useState<keyof T | null>(initial);
  const [d, setD] = useState(desc);
  const sorted = key
    ? [...rows].sort((a, b) => {
        const x = a[key] as unknown as number | string | null;
        const y = b[key] as unknown as number | string | null;
        if (x === y) return 0;
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        return (x > y ? 1 : -1) * (d ? -1 : 1);
      })
    : rows;
  const th = (k: keyof T, label: ReactNode) => (
    <th className="cursor-pointer select-none hover:text-ink" onClick={() => (key === k ? setD(!d) : (setKey(k), setD(true)))}>
      {label}
      {key === k ? (d ? " ↓" : " ↑") : ""}
    </th>
  );
  return { sorted, th };
}

/** A table whose every column sorts when its heading is clicked. ``sort`` gives the value a column
 *  sorts by (defaults to ``row[key]``); ``cell`` renders it. */
export type Column<T> = { key: string; label: ReactNode; cell: (r: T) => ReactNode; sort?: (r: T) => number | string | null | undefined; className?: string };

export function SortTable<T>({ rows, cols, rowKey, initial = null, desc = true, className = "text-sm", rowClass, onRowClick, limit }: {
  rows: T[]; cols: Column<T>[]; rowKey: (r: T, i: number) => string; initial?: string | null; desc?: boolean; className?: string;
  rowClass?: (r: T) => string; onRowClick?: (r: T) => void; limit?: number;
}) {
  const [key, setKey] = useState<string | null>(initial);
  const [d, setD] = useState(desc);
  const col = cols.find((c) => c.key === key);
  const val = (r: T) => (col?.sort ? col.sort(r) : (r as Record<string, unknown>)[key ?? ""] as number | string | null | undefined);
  const sorted = col
    ? [...rows].sort((a, b) => {
        const x = val(a), y = val(b);
        if (x === y) return 0;
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        return (x > y ? 1 : -1) * (d ? -1 : 1);
      })
    : rows;
  return (
    <div className="overflow-x-auto">
      <table className={className}>
        <thead><tr>{cols.map((c) => (
          <th key={c.key} className="cursor-pointer select-none hover:text-ink" onClick={() => (key === c.key ? setD(!d) : (setKey(c.key), setD(true)))}>
            {c.label}{key === c.key ? (d ? " ↓" : " ↑") : ""}
          </th>))}</tr></thead>
        <tbody>{(limit ? sorted.slice(0, limit) : sorted).map((r, i) => (
          <tr key={rowKey(r, i)} className={rowClass?.(r)} onClick={onRowClick ? () => onRowClick(r) : undefined}>
            {cols.map((c) => <td key={c.key} className={c.className}>{c.cell(r)}</td>)}
          </tr>))}</tbody>
      </table>
    </div>
  );
}
