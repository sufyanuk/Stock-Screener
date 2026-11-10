"use client";
/** The basket: tickers added with the hover "+" on any ticker; kept on the server until the next nightly scan. */
import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";

type Basket = { symbols: Set<string>; has: (s: string) => boolean; toggle: (s: string) => Promise<void>; reload: () => void };

const BasketContext = createContext<Basket | null>(null);

export function BasketProvider({ children }: { children: ReactNode }) {
  const [symbols, setSymbols] = useState<Set<string>>(new Set());
  const reload = useCallback(() => {
    api<{ items: { symbol: string }[] }>("/basket")
      .then((r) => setSymbols(new Set(r.items.map((i) => i.symbol))))
      .catch(() => { /* basket unavailable: keep what we have */ });
  }, []);
  // load the basket once per page load
  useEffect(() => { reload(); }, [reload]);
  const toggle = useCallback(async (s: string) => {
    const sym = s.toUpperCase();
    const inBasket = symbols.has(sym);
    setSymbols((prev) => { const n = new Set(prev); if (inBasket) n.delete(sym); else n.add(sym); return n; });  // instant feedback
    try {
      if (inBasket) await api(`/basket/${encodeURIComponent(sym)}`, { method: "DELETE" });
      else await api("/basket", { method: "POST", body: JSON.stringify({ symbol: sym }) });
    } catch {
      reload();
    }
  }, [symbols, reload]);
  const has = useCallback((s: string) => symbols.has(s.toUpperCase()), [symbols]);
  return <BasketContext.Provider value={{ symbols, has, toggle, reload }}>{children}</BasketContext.Provider>;
}

export function useBasket() {
  return useContext(BasketContext);
}
