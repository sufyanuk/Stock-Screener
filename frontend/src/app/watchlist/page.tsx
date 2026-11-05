"use client";
import { useEffect, useState } from "react";
import { ScanBar } from "@/components/scan-bar";
import { Top5 } from "@/components/top5";
import { Badge, Button, Card, Empty, ErrorBox, Field, Ticker, useSort } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { compact, nf, pct, tone, usd } from "@/lib/format";
import type { Dashboard } from "@/lib/types";

type View = "screened" | "mine";

export default function WatchlistPage() {
  const [view, setView] = useState<View>("screened");
  // remember the last sub-tab (per browser)
  useEffect(() => {
    try {
      const v = localStorage.getItem("watchlist-view");
      // eslint-disable-next-line react-hooks/set-state-in-effect -- restore the saved choice once
      if (v === "mine" || v === "screened") setView(v);
    } catch { /* storage unavailable */ }
  }, []);
  function pick(v: View) {
    setView(v);
    try { localStorage.setItem("watchlist-view", v); } catch { /* storage unavailable */ }
  }
  return (
    <div className="space-y-4">
      <div className="flex gap-1">
        {([["screened", "Top 5 & near-qualified"], ["mine", "My watchlist"]] as const).map(([k, label]) => (
          <button key={k} onClick={() => pick(k)}
            className={`rounded-md px-3 py-1.5 text-sm ${view === k ? "bg-accent text-bg" : "border border-line text-muted hover:text-ink"}`}>{label}</button>
        ))}
      </div>
      {view === "screened" ? <Screened /> : <MyWatchlist />}
    </div>
  );
}

function Screened() {
  const { data, error, reload } = useApi<Dashboard>("/dashboard", 60_000);
  const run = data?.latest_run;
  return (
    <>
      <ScanBar title="Watchlist" onScanned={reload} />
      <ErrorBox error={error} />
      <Card title="Top 5 — meets every rule" right={run && <span className="text-xs text-muted">Funnel: {run.funnel.stage1_evaluated ?? 0} under $100 → {run.funnel.stage1_options_pass ?? 0} options pass → {run.funnel.eligible ?? 0} fully eligible</span>}>
        {run ? <Top5 rows={data?.top ?? []} /> : <Empty>No completed scan yet — import today&apos;s options CSV and click “Run scan now”.</Empty>}
      </Card>
      <Card title="Near-qualified — fails one or two rules">
        {data?.watchlist.length ? (
          <div className="overflow-x-auto">
            <table className="text-sm">
              <thead><tr><th>Ticker</th><th>Company</th><th>Price</th><th>Score</th><th>Fails</th></tr></thead>
              <tbody>{data.watchlist.map((w) => (
                <tr key={w.ticker}>
                  <td><Ticker symbol={w.ticker} status={w.shariah_status} /></td>
                  <td className="max-w-56 truncate text-muted">{w.company}</td>
                  <td className="num">{usd(w.price)}</td>
                  <td className="num">{nf(w.score, 1)}</td>
                  <td className="text-xs text-bear">{w.failures.join(", ")}</td>
                </tr>))}</tbody>
            </table>
          </div>
        ) : <Empty>No near-qualified stocks in the last scan.</Empty>}
      </Card>
    </>
  );
}

type Item = {
  symbol: string; note: string | null; added_at: string; name: string | null; price: number | null; price_source: string | null;
  day_change_pct: number | null; pe: number | null; market_cap: number | null; vs_ema50_pct: number | null; above_low52_pct: number | null;
  below_high52_pct: number | null; shariah_status: string | null;
  options: { vol_change_1m_pct: number | null; iv_rank: number | null; put_call: number | null } | null;
  insider: { purchases: number; value: number; latest: string } | null;
  earnings: { date: string; time_of_day: string | null } | null;
  analysts: { strong_buy: number | null; buy: number | null } | null;
};

function MyWatchlist() {
  const [live, setLive] = useState(true);
  const { data, error, reload, loading } = useApi<{ rows: Item[] }>(`/custom-watchlist?live=${live}`);
  const [symbols, setSymbols] = useState("");
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function add() {
    setErr(null); setMsg(null);
    try {
      const r = await api<{ added: string[]; already: string[] }>("/custom-watchlist", { method: "POST", body: JSON.stringify({ symbols, note: note || null }) });
      setMsg([r.added.length ? `Added ${r.added.join(", ")}` : "", r.already.length ? `already listed: ${r.already.join(", ")}` : ""].filter(Boolean).join(" · "));
      setSymbols(""); setNote(""); reload();
    } catch (e) { setErr((e as Error).message); }
  }
  async function remove(s: string) {
    await api(`/custom-watchlist/${encodeURIComponent(s)}`, { method: "DELETE" });
    reload();
  }

  const rows = (data?.rows ?? []).map((r) => ({ ...r, opt_chg: r.options?.vol_change_1m_pct ?? null, ins_value: r.insider?.value ?? null,
    strong_buy: r.analysts?.strong_buy ?? null, earn: r.earnings?.date ?? null }));
  const { sorted, th } = useSort(rows, "added_at" as keyof (typeof rows)[number], true);
  return (
    <>
      <div>
        <h1 className="text-xl font-semibold">My watchlist</h1>
        <p className="text-sm text-muted">Any stocks you choose. Prices are live from your price provider when available, otherwise the last close from your imported price CSV.</p>
      </div>
      <Card title="Add stocks">
        <div className="flex flex-wrap items-end gap-3">
          <Field label="Tickers (separate with spaces or commas)"><input className="w-72" value={symbols} onChange={(e) => setSymbols(e.target.value.toUpperCase())}
            onKeyDown={(e) => { if (e.key === "Enter" && symbols.trim()) add(); }} placeholder="e.g. AAPL, MSFT, NVDA" /></Field>
          <Field label="Note (optional)"><input className="w-64" value={note} onChange={(e) => setNote(e.target.value)} placeholder="why you're watching" /></Field>
          <Button onClick={add} disabled={!symbols.trim()}>Add</Button>
          <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> Live prices</label>
          {msg && <span className="text-sm text-bull">{msg}</span>}
        </div>
        <div className="mt-2"><ErrorBox error={err || error} /></div>
      </Card>
      <Card title={`${rows.length} stocks`}>
        {loading && !data ? <Empty>Loading…</Empty> : sorted.length ? (
          <div className="overflow-x-auto">
            <table className="text-sm">
              <thead><tr>
                {th("symbol", "Ticker")}<th>Company</th>{th("price", "Price")}{th("day_change_pct", "Day")}{th("pe", "P/E")}{th("market_cap", "Mkt cap")}
                {th("vs_ema50_pct", "vs 50 EMA")}{th("above_low52_pct", "Above 52w low")}{th("below_high52_pct", "From 52w high")}
                {th("opt_chg", "Options 1M Δ")}{th("ins_value", "Insider buys 90d")}{th("strong_buy", "Strong Buy")}{th("earn", "Earnings")}<th>Note</th><th></th>
              </tr></thead>
              <tbody>{sorted.map((r) => (
                <tr key={r.symbol}>
                  <td><Ticker symbol={r.symbol} status={r.shariah_status} /></td>
                  <td className="max-w-44 truncate text-muted">{r.name ?? "—"}</td>
                  <td className="num" title={r.price_source ?? undefined}>{usd(r.price)}</td>
                  <td className={`num ${tone(r.day_change_pct)}`}>{pct(r.day_change_pct, 2, true)}</td>
                  <td className="num">{nf(r.pe, 1)}</td>
                  <td className="num">{r.market_cap ? `$${compact(r.market_cap)}` : "—"}</td>
                  <td className={`num ${tone(r.vs_ema50_pct)}`}>{pct(r.vs_ema50_pct, 1, true)}</td>
                  <td className="num">{pct(r.above_low52_pct, 1, true)}</td>
                  <td className={`num ${tone(r.below_high52_pct)}`}>{pct(r.below_high52_pct, 1, true)}</td>
                  <td className="num">{r.options ? <span className="text-bull">{pct(r.options.vol_change_1m_pct, 0, true)}</span> : "—"}</td>
                  <td className="num">{r.insider ? <span className="text-bull" title={`${r.insider.purchases} purchase(s), latest ${r.insider.latest}`}>${compact(r.insider.value)}</span> : "—"}</td>
                  <td className="num">{r.analysts ? <Badge tone={(r.analysts.strong_buy ?? 0) > 0 ? "bull" : "muted"}>{r.analysts.strong_buy ?? 0}</Badge> : "—"}</td>
                  <td className="num text-xs">{r.earnings?.date ?? "—"}</td>
                  <td className="max-w-48 truncate text-xs text-muted" title={r.note ?? undefined}>{r.note}</td>
                  <td><Button variant="ghost" onClick={() => remove(r.symbol)}>Remove</Button></td>
                </tr>))}</tbody>
            </table>
          </div>
        ) : <Empty>Your watchlist is empty — add tickers above.</Empty>}
        <p className="mt-2 text-xs text-muted">— = no data. EMA and 52-week figures come from the imported price CSV; options from the latest options CSV; insider buys from SEC filings; Strong Buy counts from the Earnings tab cache or the stock panel.</p>
      </Card>
    </>
  );
}
