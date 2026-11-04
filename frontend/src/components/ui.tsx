"use client";
import Link from "next/link";
import { ReactNode, useState } from "react";

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

export function Ticker({ symbol, status, link = true, className = "" }: { symbol: string; status?: string | null; link?: boolean; className?: string }) {
  const title = status === "Compliant" ? "Shariah-compliant" : status === "Non-Compliant" ? "Not Shariah-compliant" : "Shariah status not available";
  const cls = `font-semibold ${shariahTone(status)} ${className}`;
  return link ? <Link href={`/stock/${symbol}`} title={title} className={`${cls} hover:underline`}>{symbol}</Link> : <span title={title} className={cls}>{symbol}</span>;
}

export function ShariahLegend() {
  return (
    <span className="text-xs text-muted">
      <span className="font-semibold text-bull">Green</span> = Shariah-compliant · <span className="font-semibold text-bear">red</span> = non-compliant ·{" "}
      <span className="font-semibold text-accent">blue</span> = not yet screened
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
