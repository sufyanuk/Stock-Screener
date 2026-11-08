"use client";
import { useEffect, useRef, useState } from "react";
import { Button, Card, Empty, ErrorBox, Field, ShariahLegend, SortTable, Ticker } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { nf, pct, tone } from "@/lib/format";
import { PriceImport } from "@/components/price-import";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

type Mode = "bullish" | "bearish";
type Entry = { rank: number; symbol: string; name: string | null; years: number; count: number; pct: number | null;
  max: number | null; min: number | null; avg_dir: number | null; avg: number; last_close: number | null; shariah_status: string | null };
type Coverage = { first_month: string | null; last_month: string | null; months: number; symbols: number; provider: string | null; computed_at: string | null };
type Load = { universe: number; loaded: number; remaining: number; stale: number; target_month: string };
type Resp = { month: number; month_name: string; mode: Mode; ranked: Entry[]; total_matching: number; note: string; coverage: Coverage; load: Load;
  index: string; indexes: { code: string; label: string }[]; index_error: string | null;
  best_by_month: { month: number; month_name: string; top: { symbol: string; avg: number; win_rate: number; years: number; shariah_status?: string | null }[] }[] };

const num = (v: string) => v.replace(/[^0-9.]/g, "");

export default function SeasonalityPage() {
  const [month, setMonth] = useState(new Date().getMonth() + 1);
  const [index, setIndex] = useState("sp500");
  const [mode, setMode] = useState<Mode>("bullish");
  const [minYears, setMinYears] = useState(5);
  const [minPrice, setMinPrice] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const qs = new URLSearchParams({ month: String(month), index, mode, min_years: String(minYears), limit: "1000" });
  if (minPrice) qs.set("min_price", minPrice);
  if (maxPrice) qs.set("max_price", maxPrice);
  if (q) qs.set("q", q);
  const { data, error, reload, loading } = useApi<Resp>(`/seasonality?${qs}`);
  const cov = data?.coverage;
  const ld = data?.load;
  const bear = mode === "bearish";
  const word = bear ? "Negative" : "Positive";

  async function load(rebuild = false) {
    setErr(null);
    setBusy("Loading 10 years of monthly prices for all US stocks…");
    try {
      for (let i = 0; i < 30; i++) {
        const r = await api<Load & { processed: number; errors: string[] }>("/seasonality/load", {
          method: "POST", body: JSON.stringify({ max_seconds: 200, rebuild: rebuild && i === 0 }) });
        setBusy(`Loaded ${(r.universe - r.remaining).toLocaleString()} of ${r.universe.toLocaleString()} stocks…`);
        if (r.errors.length) setErr(r.errors[0]);
        if ((r.remaining <= 0 && r.stale <= 0) || r.processed === 0) break;
      }
    } catch (e) {
      setErr((e as Error).message);
    }
    setBusy(null);
    reload();
  }

  // Load automatically when the monthly history is missing or a month behind (once per page visit).
  const auto = useRef(false);
  useEffect(() => {
    if (!ld || auto.current) return;
    if (ld.remaining > 0 || ld.stale > 0) {
      auto.current = true;
      // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time automatic load when history is missing or stale
      load();
    }
  }, [ld]); // eslint-disable-line react-hooks/exhaustive-deps

  const label = data?.indexes.find((i) => i.code === index)?.label ?? "";
  const bg = bear ? "bg-bear/10" : "bg-bull/10";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Seasonality</h1>
          <p className="text-sm text-muted">
            How often each stock rose (or fell) in a calendar month over the past years, month-end to month-end (Alpaca monthly bars, adjusted for splits and dividends).
            {cov && cov.months > 0 && <> Data: {cov.symbols.toLocaleString()} stocks · {cov.first_month?.slice(0, 7)} → {cov.last_month?.slice(0, 7)}</>}
          </p>
        </div>
        <ShariahLegend />
      </div>

      <Card>
        <div className="flex flex-wrap items-end gap-4">
          <Field label="Select Index">
            <select value={index} onChange={(e) => setIndex(e.target.value)}>
              {(data?.indexes ?? [{ code: "sp500", label: "S&P 500" }]).map((i) => <option key={i.code} value={i.code}>{i.label}</option>)}
            </select>
          </Field>
          <Field label="Select Month">
            <select value={month} onChange={(e) => setMonth(Number(e.target.value))}>
              {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
            </select>
          </Field>
          <div className="flex items-center gap-4 pb-2 text-sm">
            {(["bullish", "bearish"] as Mode[]).map((m) => (
              <label key={m} className="flex cursor-pointer items-center gap-1.5">
                <input type="radio" name="mode" checked={mode === m} onChange={() => setMode(m)} />
                <span className={m === "bullish" ? "text-bull" : "text-bear"}>{m === "bullish" ? "Bullish" : "Bearish"}</span>
              </label>
            ))}
          </div>
        </div>
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-muted">More filters</summary>
          <div className="mt-2 grid grid-cols-2 gap-3 md:grid-cols-4">
            <Field label="Minimum years of history"><input type="number" min={1} max={20} value={minYears} onChange={(e) => setMinYears(Number(e.target.value) || 1)} /></Field>
            <Field label="Price from ($)"><input value={minPrice} onChange={(e) => setMinPrice(num(e.target.value))} placeholder="any" /></Field>
            <Field label="Price below ($)"><input value={maxPrice} onChange={(e) => setMaxPrice(num(e.target.value))} placeholder="any" /></Field>
            <Field label="Ticker starts with"><input value={q} onChange={(e) => setQ(e.target.value.toUpperCase())} placeholder="e.g. AA" /></Field>
          </div>
        </details>
        <div className="mt-3 space-y-2">
          <ErrorBox error={error} />
          {data?.index_error && <div className="text-sm text-warn">{data.index_error}</div>}
          {err && <div className="text-sm text-bear">{err}</div>}
          {busy && <div className="text-sm text-accent">{busy}</div>}
        </div>
      </Card>

      <Card title={data ? `${label} · ${MONTHS[data.month - 1]} · ${bear ? "Bearish" : "Bullish"} — ${data.total_matching.toLocaleString()} stocks` : "Seasonality"}
        right={ld && <Button onClick={() => load()} disabled={!!busy}>{busy ? "Loading…" : "Update prices"}</Button>}>
        {data?.ranked.length ? (
          <SortTable rows={data.ranked} rowKey={(e) => e.symbol} initial="pct" limit={500}
            cols={[
              { key: "symbol", label: "Stock Name", cell: (e) => (
                <div className="flex flex-col"><Ticker symbol={e.symbol} status={e.shariah_status} />
                  {e.name && <span className="max-w-[16rem] truncate text-xs text-muted" title={e.name}>{e.name}</span>}</div>) },
              { key: "years", label: "Total Years", cell: (e) => e.years, className: "num" },
              { key: "count", label: `Total ${word} Years`, cell: (e) => e.count, className: "num" },
              { key: "pct", label: `% ${word}`, cell: (e) => <span className={`rounded px-1.5 py-0.5 font-semibold ${bg}`}>{e.pct === null ? "—" : `${nf(e.pct, 2)}%`}</span>, className: "num" },
              { key: "max", label: `Max ${word} Change (%)`, sort: (e) => (e.max === null ? null : Math.abs(e.max)), cell: (e) => <span className={tone(e.max)}>{pct(e.max, 2)}</span>, className: "num" },
              { key: "min", label: `Min ${word} Change (%)`, sort: (e) => (e.min === null ? null : Math.abs(e.min)), cell: (e) => <span className={tone(e.min)}>{pct(e.min, 2)}</span>, className: "num" },
              { key: "avg_dir", label: `Average ${word} Change (%)`, sort: (e) => (e.avg_dir === null ? null : Math.abs(e.avg_dir)), cell: (e) => <span className={`font-semibold ${tone(e.avg_dir)}`}>{pct(e.avg_dir, 2)}</span>, className: "num" },
            ]} />
        ) : <Empty>{loading ? "Loading…" : busy ? "Loading price history — results appear when it finishes." : cov?.months ? "No stocks match these filters." : "No price history yet."}</Empty>}
        <p className="mt-2 text-xs text-muted">{data?.note} Max/Min/Average are over the {bear ? "down" : "up"} years only. Monthly moves beyond ±200% are treated as data errors and ignored.</p>
      </Card>

      <Card title="Top 5 for each month (average return, all US stocks)">
        {data?.best_by_month.some((m) => m.top.length) ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
            {data.best_by_month.map((m) => (
              <div key={m.month} className={`rounded-lg border p-2 ${m.month === month ? "border-accent" : "border-line"}`}>
                <button className="mb-1 text-sm font-semibold hover:text-accent" onClick={() => setMonth(m.month)}>{m.month_name}</button>
                {m.top.length ? m.top.map((t) => (
                  <div key={t.symbol} className="flex justify-between text-xs"><Ticker symbol={t.symbol} status={t.shariah_status} />
                    <span className={`num ${tone(t.avg)}`}>{pct(t.avg, 1, true)} <span className="text-muted">{nf(t.win_rate, 0)}%</span></span></div>
                )) : <div className="text-xs text-muted">—</div>}
              </div>
            ))}
          </div>
        ) : <Empty>Appears once the price history is loaded.</Empty>}
      </Card>

      {data && <PriceImport open={false} onDone={reload} />}
    </div>
  );
}
