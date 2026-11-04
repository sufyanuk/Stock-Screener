"use client";
import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";

export type EarningsMarker = { date: string; time_of_day: string | null };

type PaneState = {
  selected: string | null;
  select: (symbol: string | null) => void;
  earnings: Record<string, EarningsMarker>;
};

const PaneContext = createContext<PaneState | null>(null);

/** Holds the stock shown in the right-hand panel and the "earnings within 7 days" markers. */
export function PaneProvider({ children }: { children: ReactNode }) {
  const [selected, setSelected] = useState<string | null>(null);
  const [earnings, setEarnings] = useState<Record<string, EarningsMarker>>({});
  useEffect(() => {
    api<{ markers: Record<string, EarningsMarker> }>("/earnings/markers?days=7")
      .then((r) => setEarnings(r.markers))
      .catch(() => setEarnings({}));
  }, []);
  const select = useCallback((s: string | null) => setSelected(s ? s.toUpperCase() : null), []);
  return <PaneContext.Provider value={{ selected, select, earnings }}>{children}</PaneContext.Provider>;
}

export function usePane() {
  return useContext(PaneContext);
}

export function daysUntil(iso: string) {
  const d = new Date(iso + "T00:00:00");
  const t = new Date();
  t.setHours(0, 0, 0, 0);
  return Math.round((d.getTime() - t.getTime()) / 864e5);
}

export function earningsLabel(m: EarningsMarker) {
  const n = daysUntil(m.date);
  const when = n <= 0 ? "today" : n === 1 ? "tomorrow" : `in ${n} days`;
  const tod = m.time_of_day ? ` (${m.time_of_day.replace("-", " ")})` : "";
  return `Earnings ${m.date}${tod} — ${when}`;
}
