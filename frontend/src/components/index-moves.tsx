"use client";
/** Dashboard line: S&P 500 (SPY) and Nasdaq-100 (QQQ) % change from Alpaca, including pre-market (from 4:00 am
 *  New York time). Not cached for the day: refreshed every 2 minutes while the page is visible. */
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { tone } from "@/lib/format";

type Moves = { phase: string; rows: { symbol: string; label: string; pct: number | null; price: number | null; as_of: string | null }[] };

const PHASE: Record<string, string> = { "pre-market": "pre-market", open: "today", "after-hours": "after hours" };

export function IndexMoves() {
  const [m, setM] = useState<Moves | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => {
      if (document.visibilityState !== "visible") return;
      api<Moves>("/index-moves").then((r) => { if (alive) setM(r); }).catch(() => { /* line stays as it was */ });
    };
    load();
    const t = setInterval(load, 120e3);
    document.addEventListener("visibilitychange", load);
    return () => { alive = false; clearInterval(t); document.removeEventListener("visibilitychange", load); };
  }, []);
  if (!m || !m.rows.length || m.rows.every((r) => r.pct == null)) return null;
  const when = PHASE[m.phase] ?? "last session";
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm" title="SPY and QQQ from Alpaca; before the open this is their pre-market move, which tracks the index futures closely">
      {m.rows.map((r) => (
        <span key={r.symbol}>
          <span className="text-muted">{r.label} </span>
          <b className={`num ${tone(r.pct)}`}>{r.pct == null ? "—" : `${r.pct > 0 ? "+" : ""}${r.pct.toFixed(2)}%`}</b>
          <span className="text-xs text-muted"> ({r.symbol})</span>
        </span>
      ))}
      <span className="text-xs text-muted">{when}</span>
    </div>
  );
}
