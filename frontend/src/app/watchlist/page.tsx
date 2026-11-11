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
  const { data, error, reload, loading } = useApi<{ rows: Item[] }>("/custom-watchlist");  // live prices from the APIs
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

  const rows = (data?.rows ?? []).map((r) => {
    const b = r.bars.at(-1), p = r.bars.at(-2);
    return { ...r, date: b?.date ?? null, open: b?.open ?? null, high: b?.high ?? null, low: b?.low ?? null, close: b?.close ?? null,
      volume: b?.volume ?? null, change: b && p ? (b.close / p.close - 1) * 100 : null };
  });
  const { sorted, th } = useSort(rows, null);

  return (
    <Card title="Custom watchlist">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Add tickers (spaces or commas)"><input className="w-64" value={symbols} onChange={(e) => setSymbols(e.target.value.toUpperCase())}
          onKeyDown={(e) => { if (e.key === "Enter" && symbols.trim()) add(); }} placeholder="e.g. AAPL, MSFT, NVDA" /></Field>
        <Field label="Note (optional)"><input className="w-56" value={note} onChange={(e) => setNote(e.target.value)} placeholder="why you're watching" /></Field>
        <Button onClick={add} disabled={!symbols.trim()}>Add</Button>
        {msg && <span className="text-sm text-bull">{msg}</span>}
      </div>
      <div className="mt-2"><ErrorBox error={err || error} /></div>

      {loading && !data ? <Empty>Loading…</Empty> : sorted.length ? (
        <div className="mt-3 overflow-x-auto">
          <table className="text-sm">
            <thead><tr>
              {th("symbol", "Ticker")}{th("name", "Company")}{th("close", "Price")}{th("date", "Date")}{th("open", "Open")}{th("high", "High")}{th("low", "Low")}
              {th("close", "Close")}{th("change", "Change")}{th("volume", "Volume")}{th("note", "Note")}<th></th>
            </tr></thead>
            <tbody>{sorted.map((r) => (
              <tr key={r.symbol}>
                <td><Ticker symbol={r.symbol} status={r.shariah_status} /></td>
                <td className="max-w-44 truncate text-muted">{r.name ?? "—"}</td>
                <td className="num font-semibold">{usd(r.close)}</td>
                <td className="text-xs text-muted" title={r.bars_source ?? undefined}>{r.date ?? "—"}</td>
                <td className="num">{usd(r.open)}</td><td className="num">{usd(r.high)}</td><td className="num">{usd(r.low)}</td>
                <td className="num font-semibold">{usd(r.close)}</td>
                <td className={`num ${tone(r.change)}`}>{pct(r.change, 2, true)}</td>
                <td className="num">{compact(r.volume)}</td>
                <td className="max-w-48 truncate text-xs text-muted" title={r.note ?? undefined}>{r.note}</td>
                <td><Button variant="ghost" onClick={() => remove(r.symbol)}>Remove</Button></td>
              </tr>))}</tbody>
          </table>
        </div>
      ) : <Empty>Your watchlist is empty — add tickers above.</Empty>}

      <p className="mt-2 text-xs text-muted">Current trading day&apos;s open, high, low, close and volume per stock (change vs the previous close), from the price APIs.</p>
    </Card>
  );
}
