"use client";
import { useState } from "react";
import { Button, Card, Empty, ErrorBox, Field, Ticker, useSort } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { compact, pct, tone, usd } from "@/lib/format";

export default function WatchlistPage() {
  return (
    <div className="space-y-4">
      <div><h1 className="text-xl font-semibold">Watchlist</h1><p className="text-sm text-muted">Your own list of stocks with their latest daily prices.</p></div>
      <CustomWatchlist />
    </div>
  );
}

type Bar = { date: string; open: number | null; high: number | null; low: number | null; close: number; volume: number | null };
type Item = { symbol: string; note: string | null; added_at: string; name: string | null; shariah_status: string | null; bars: Bar[]; bars_source: string | null };

function CustomWatchlist() {
  const [live, setLive] = useState(true);
  const { data, error, reload, loading } = useApi<{ rows: Item[] }>(`/custom-watchlist?live=${live}`);
  const [symbols, setSymbols] = useState("");
  const [note, setNote] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function add() {
    setErr(null); setMsg(null);
    try {
      const r = await api<{ added: string[]; already: string[] }>("/custom-watchlist", { method: "POST", body: JSON.stringify({ symbols, note: note || null }) });
      setMsg([r.added.length ? `Added ${r.added.join(", ")}` : "", r.already.length ? `already listed: ${r.already.join(", ")}` : ""].filter(Boolean).join(" · "));
      if (r.added.length) setSel(r.added[0]);
      setSymbols(""); setNote(""); reload();
    } catch (e) { setErr((e as Error).message); }
  }
  async function remove(s: string) {
    await api(`/custom-watchlist/${encodeURIComponent(s)}`, { method: "DELETE" });
    if (sel === s) setSel(null);
    reload();
  }

  const rows = (data?.rows ?? []).map((r) => {
    const b = r.bars.at(-1), p = r.bars.at(-2);
    return { ...r, date: b?.date ?? null, open: b?.open ?? null, high: b?.high ?? null, low: b?.low ?? null, close: b?.close ?? null,
      volume: b?.volume ?? null, change: b && p ? (b.close / p.close - 1) * 100 : null };
  });
  const { sorted, th } = useSort(rows, null);
  const chosen = rows.find((r) => r.symbol === sel) ?? null;

  return (
    <Card title="Custom watchlist" right={
      <label className="flex items-center gap-1.5 text-sm"><input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> Live prices from API</label>}>
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Add tickers (spaces or commas)"><input className="w-64" value={symbols} onChange={(e) => setSymbols(e.target.value.toUpperCase())}
          onKeyDown={(e) => { if (e.key === "Enter" && symbols.trim()) add(); }} placeholder="e.g. AAPL, MSFT, NVDA" /></Field>
        <Field label="Note (optional)"><input className="w-56" value={note} onChange={(e) => setNote(e.target.value)} placeholder="why you're watching" /></Field>
        <Button onClick={add} disabled={!symbols.trim()}>Add</Button>
        {rows.length > 0 && (
          <Field label="Select stock">
            <select value={sel ?? ""} onChange={(e) => setSel(e.target.value || null)}>
              <option value="">— choose —</option>
              {rows.map((r) => <option key={r.symbol} value={r.symbol}>{r.symbol}</option>)}
            </select>
          </Field>
        )}
        {msg && <span className="text-sm text-bull">{msg}</span>}
      </div>
      <div className="mt-2"><ErrorBox error={err || error} /></div>

      {loading && !data ? <Empty>Loading…</Empty> : sorted.length ? (
        <div className="mt-3 overflow-x-auto">
          <table className="text-sm">
            <thead><tr>
              {th("symbol", "Ticker")}<th>Company</th>{th("date", "Date")}{th("open", "Open")}{th("high", "High")}{th("low", "Low")}
              {th("close", "Close")}{th("change", "Change")}{th("volume", "Volume")}<th>Note</th><th></th>
            </tr></thead>
            <tbody>{sorted.map((r) => (
              <tr key={r.symbol} onClick={() => setSel(r.symbol)} className={`cursor-pointer ${sel === r.symbol ? "bg-panel-2" : ""}`}>
                <td><Ticker symbol={r.symbol} status={r.shariah_status} /></td>
                <td className="max-w-44 truncate text-muted">{r.name ?? "—"}</td>
                <td className="text-xs text-muted" title={r.bars_source ?? undefined}>{r.date ?? "—"}</td>
                <td className="num">{usd(r.open)}</td><td className="num">{usd(r.high)}</td><td className="num">{usd(r.low)}</td>
                <td className="num font-semibold">{usd(r.close)}</td>
                <td className={`num ${tone(r.change)}`}>{pct(r.change, 2, true)}</td>
                <td className="num">{compact(r.volume)}</td>
                <td className="max-w-48 truncate text-xs text-muted" title={r.note ?? undefined}>{r.note}</td>
                <td><Button variant="ghost" onClick={(e) => { e.stopPropagation(); remove(r.symbol); }}>Remove</Button></td>
              </tr>))}</tbody>
          </table>
        </div>
      ) : <Empty>Your watchlist is empty — add tickers above.</Empty>}

      {chosen && (
        <div className="mt-4">
          <h3 className="mb-1 text-sm font-semibold">{chosen.symbol} — daily open, high, low, close and volume
            <span className="ml-2 text-xs font-normal text-muted">{chosen.bars_source ? `source: ${chosen.bars_source}` : ""}</span></h3>
          {chosen.bars.length ? (
            <div className="overflow-x-auto">
              <table className="text-sm">
                <thead><tr><th>Date</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Change</th><th>Volume</th></tr></thead>
                <tbody>{[...chosen.bars].reverse().map((b, i, arr) => {
                  const prev = arr[i + 1];
                  const ch = prev ? (b.close / prev.close - 1) * 100 : null;
                  return (
                    <tr key={b.date}><td>{b.date}</td><td className="num">{usd(b.open)}</td><td className="num">{usd(b.high)}</td>
                      <td className="num">{usd(b.low)}</td><td className="num font-semibold">{usd(b.close)}</td>
                      <td className={`num ${tone(ch)}`}>{pct(ch, 2, true)}</td><td className="num">{compact(b.volume)}</td></tr>
                  );
                })}</tbody>
              </table>
            </div>
          ) : <Empty>No price bars for {chosen.symbol} — set MASSIVE_API_KEY or FMP_API_KEY for live data, or import a price CSV that includes it.</Empty>}
        </div>
      )}
      <p className="mt-2 text-xs text-muted">Latest daily bar per stock; click a row or use “Select stock” for the last 15 sessions. Prices come from your price API when available, otherwise from the imported price CSV.</p>
    </Card>
  );
}
