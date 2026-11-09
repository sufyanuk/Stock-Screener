"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";
import { StockPane } from "@/components/stock-pane";
import { TickerSearch } from "@/components/ticker-search";
import { api, JOBS_ON_GITHUB, useApi } from "@/lib/api";
import { PaneProvider, usePane } from "@/lib/pane-context";

const NAV = [
  ["/dashboard", "Dashboard"],
  ["/options", "Unusual Options"],
  ["/insiders", "Insider Buying"],
  ["/watchlist", "Watchlist"],
  ["/earnings", "Earnings"],
  ["/ratings", "Ratings"],
  ["/seasonality", "Seasonality"],
  ["/shariah", "Shariah"],
  ["/tools", "Calculators"],
] as const;

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const [menu, setMenu] = useState(false);
  // Personal app: no sign-in. Access is restricted by Vercel Authentication on the deployment.
  return (
    <PaneProvider>
    <div className="min-h-screen">
      <nav className="sticky top-0 z-20 border-b border-line bg-bg/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1900px] items-center gap-4 px-4 py-2.5">
          <Link href="/dashboard" className="flex items-center gap-2 font-semibold">
            <span className="grid h-7 w-7 place-items-center rounded-md bg-accent/20 text-accent">SI</span>
            <span>Stock Intel</span>
          </Link>
          <TickerSearch className="ml-auto w-40 sm:w-56 md:order-last md:ml-2" />
          <button className="md:hidden" onClick={() => setMenu(!menu)} aria-label="menu">☰</button>
          <div className={`${menu ? "flex" : "hidden"} absolute left-0 right-0 top-12 flex-col gap-1 border-b border-line bg-bg p-3 md:static md:flex md:flex-row md:border-0 md:p-0`}>
            {NAV.map(([href, label]) => (
              <Link key={href} href={href} onClick={() => setMenu(false)}
                className={`rounded-md px-2.5 py-1.5 text-sm ${path.startsWith(href) ? "bg-panel-2 text-ink" : "text-muted hover:text-ink"}`}>
                {label}
              </Link>
            ))}
          </div>
        </div>
      </nav>
      {!JOBS_ON_GITHUB && <DailyAutoScan />}
      <MainWithPane>{children}</MainWithPane>
      <footer className="mx-auto max-w-[1900px] px-4 pb-8 text-xs text-muted">
        Research tool only — not investment advice. Data may be delayed or incomplete; every value shows its source and timestamp.
        Shariah highlighting (green / red) is an analytical aid, not a fatwa.
        <ProviderStatus />
      </footer>
    </div>
    </PaneProvider>
  );
}

const PROVIDERS: [string, string][] = [["alpaca", "Alpaca"], ["finnhub", "Finnhub"], ["tiingo", "Tiingo"], ["sentisense", "SentiSense"], ["alphavantage", "Alpha Vantage"],
  ["fmp", "FMP"], ["openinsider", "OpenInsider"], ["sec_edgar", "SEC EDGAR"], ["massive", "Massive"]];

/** Which data APIs have keys set on the server. */
function ProviderStatus() {
  const { data } = useApi<{ providers_configured: Record<string, string> }>("/health");
  if (!data) return null;
  const p = data.providers_configured;
  return (
    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
      <span>Data APIs:</span>
      {PROVIDERS.filter(([k]) => k in p).map(([k, label]) => {
        const ok = p[k] === "configured";
        return <span key={k} className={ok ? "text-bull" : "text-muted"} title={p[k]}>{ok ? "●" : "○"} {label}</span>;
      })}
    </div>
  );
}

/** The first time the app is opened on a new day, run a scan automatically (the server skips it if a
 *  scan already ran today, so several tabs or devices cannot start duplicates). */
function DailyAutoScan() {
  const [state, setState] = useState<"idle" | "running" | "done" | "error">("idle");
  const [msg, setMsg] = useState("");
  useEffect(() => {
    const today = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
    try { if (localStorage.getItem("auto-scan-day") === today) return; } catch { /* storage unavailable */ }
    try { localStorage.setItem("auto-scan-day", today); } catch { /* storage unavailable */ }
    let cancelled = false;
    let finished = false;
    (async () => {
      try {
        const r = await api<{ ran: boolean }>("/scan/daily", { method: "POST", body: "{}" });
        finished = true;
        if (cancelled) return;
        if (r.ran) { setState("done"); setMsg("Today's first scan has finished."); } else setState("idle");
      } catch (e) {
        finished = true;
        if (!cancelled) { setState("error"); setMsg(`Daily scan: ${(e as Error).message}`); }
      }
    })();
    // show progress only if the request takes a moment (i.e. a scan is actually running)
    const t = setTimeout(() => { if (!cancelled && !finished) setState("running"); }, 1500);
    return () => { cancelled = true; clearTimeout(t); };
  }, []);
  if (state === "idle") return null;
  return (
    <div className="mx-auto max-w-[1900px] px-4 pt-3">
      <div className={`flex flex-wrap items-center gap-3 rounded-lg border p-2 text-sm ${state === "error" ? "border-warn/40 bg-warn/10 text-warn" : "border-accent/40 bg-accent/10"}`}>
        {state === "running" ? <span>Running today&apos;s first scan… (insider buying, earnings and analysts are refreshed)</span> : <span>{msg}</span>}
        {state === "done" && <button className="underline" onClick={() => window.location.reload()}>Refresh the page</button>}
        {state !== "running" && <button className="ml-auto text-muted hover:text-ink" onClick={() => setState("idle")}>Dismiss</button>}
      </div>
    </div>
  );
}

/** Content on the left, selected-stock panel on the right (slide-over on small screens). */
function MainWithPane({ children }: { children: ReactNode }) {
  const pane = usePane();
  const open = !!pane?.selected;
  // One panel instance: a sticky right column on large screens (always visible), and a slide-over
  // on small screens that appears only when a stock is selected.
  const place = open
    ? "fixed inset-y-0 right-0 z-40 w-[min(420px,100vw)] p-2"
    : "hidden";
  return (
    <main className="mx-auto max-w-[1900px] px-4 py-5">
      <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_400px] lg:gap-4">
        <div className="min-w-0">{children}</div>
        {open && <div className="fixed inset-0 z-30 bg-black/50 lg:hidden" onClick={() => pane?.select(null)} />}
        <div className={`${place} lg:sticky lg:inset-auto lg:top-16 lg:z-auto lg:block lg:w-auto lg:self-start lg:p-0`}>
          <StockPane />
        </div>
      </div>
    </main>
  );
}
