"use client";
import { FormEvent, useState } from "react";
import { usePane } from "@/lib/pane-context";

/** Type a ticker and press Enter to open it in the right-hand panel. */
export function TickerSearch({ className = "", autoFocus = false }: { className?: string; autoFocus?: boolean }) {
  const pane = usePane();
  const [q, setQ] = useState("");
  function submit(e: FormEvent) {
    e.preventDefault();
    const t = q.trim().toUpperCase();
    if (!t || !/^[A-Z][A-Z0-9.\-]{0,9}$/.test(t)) return;
    pane?.select(t);
    setQ("");
  }
  return (
    <form onSubmit={submit} className={className} role="search">
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Look up ticker, e.g. AAPL" aria-label="Look up a ticker"
        autoFocus={autoFocus} className="w-full text-sm uppercase placeholder:normal-case" />
    </form>
  );
}
