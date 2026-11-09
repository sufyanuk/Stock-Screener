"use client";
import { useEffect, useRef, useState } from "react";
import { Button, Card, Empty, ErrorBox, Field, ShariahLegend, SortTable, Ticker } from "@/components/ui";
import { api, JOBS_NOTE, JOBS_ON_GITHUB, useApi } from "@/lib/api";
import { nf, pct, tone, usd } from "@/lib/format";
import { PriceImport } from "@/components/price-import";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

type Entry = { rank: number; symbol: string; name: string | null; years: number; count: number; pct: number | null;
  max: number | null; min: number | null; avg_dir: number | null; avg: number; last_close: number | null;
  price: number | null; price_as_of: string | null; price_live: boolean; shariah_status: string | null };
type Coverage = { first_month: string | null; last_month: string | null; months: number; symbols: number; provider: string | null; computed_at: string | null };
type Load = { universe: number; loaded: number; remaining: number; stale: number; target_month: string };
type Resp = { month: number; month_name: string; ranked: Entry[]; total_matching: number; note: string; coverage: Coverage; load: Load };

const num = (v: string) => v.replace(/[^0-9.]/g, "");

export default function SeasonalityPage() {
  const [month, setMonth] = useState(new Date().getMonth() + 1);
  const [rankBy, setRankBy] = useState("pct");
  const [minYears, setMinYears] = useState(5);
  const [minPrice, setMinPrice] = useState("5");
  const [maxPrice, setMaxPrice] = useState("100");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const qs = new URLSearchParams({ month: String(month), rank_by: rankBy, min_years: String(minYears), limit: "1000" });
  if (minPrice) qs.set("min_price", minPrice);
  if (maxPrice) qs.set("max_price", maxPrice);
  if (q) qs.set("q", q);
  const { data, error, reload, loading } = useApi<Resp>(`/seasonality?${qs}`);
  const cov = data?.coverage;
  const ld = data?.load;

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
    if (!ld || auto.current || JOBS_ON_GITHUB) return;
    if (ld.remaining > 0 || ld.stale > 0) {
      auto.current = true;
      // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time automatic load when history is missing or stale
      load();
    }
  }, [ld]); // eslint-disable-line react-hooks/exhaustive-deps


  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Seasonality</h1>
          <p className="text-sm text-muted">
            How often each stock rose in a calendar month over the past years, month-end to month-end (Alpaca monthly bars, adjusted for splits and dividends).
            {cov && cov.months > 0 && <> Data: {cov.symbols.toLocaleString()} stocks · {cov.first_month?.slice(0, 7)} → {cov.last_month?.slice(0, 7)}</>}
          </p>
        </div>
        <ShariahLegend />
      </div>

      {(data || error) && <PriceImport open={!cov?.months} onDone={reload} />}

      <Card>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <Field label="Rank by"><select value={rankBy} onChange={(e) => setRankBy(e.target.value)}><option value="pct">% of years positive</option><option value="avg">Average positive change</option></select></Field>
          <Field label="Minimum years of history"><input type="number" min={1} max={20} value={minYears} onChange={(e) => setMinYears(Number(e.target.value) || 1)} /></Field>
          <Field label="Price from ($)"><input value={minPrice} onChange={(e) => setMinPrice(num(e.target.value))} placeholder="any" /></Field>
          <Field label="Price below ($)"><input value={maxPrice} onChange={(e) => setMaxPrice(num(e.target.value))} placeholder="any" /></Field>
          <Field label="Ticker starts with"><input value={q} onChange={(e) => setQ(e.target.value.toUpperCase())} placeholder="e.g. AA" /></Field>
        </div>
        <div className="mt-3 flex flex-wrap gap-1">
          {MONTHS.map((m, i) => (
            <button key={m} onClick={() => setMonth(i + 1)}
              className={`rounded-md px-3 py-1.5 text-sm ${month === i + 1 ? "bg-accent text-bg" : "border border-line text-muted hover:text-ink"}`}>{m.slice(0, 3)}</button>
          ))}
        </div>
        <div className="mt-3 space-y-2">
          <ErrorBox error={error} />
          {err && <div className="text-sm text-bear">{err}</div>}
          {busy && <div className="text-sm text-accent">{busy}</div>}
        </div>
      </Card>

      <Card title={data ? `Best stocks in ${MONTHS[data.month - 1]} — top ${data.ranked.length.toLocaleString()} of ${data.total_matching.toLocaleString()}` : "Seasonality"}
        right={JOBS_ON_GITHUB ? <span className="text-xs text-muted">Prices: {JOBS_NOTE}</span> : ld && <Button onClick={() => load()} disabled={!!busy}>{busy ? "Loading…" : "Update prices"}</Button>}>
        {data?.ranked.length ? (
          <SortTable rows={data.ranked} rowKey={(e) => e.symbol} initial={null} limit={500}
            cols={[
              { key: "symbol", label: "Ticker", cell: (e) => <Ticker symbol={e.symbol} status={e.shariah_status} /> },
              { key: "name", label: "Company", cell: (e) => <span className="block max-w-[18rem] truncate" title={e.name ?? ""}>{e.name ?? "—"}</span> },
              { key: "price", label: "Price", cell: (e) => <span title={e.price_live ? "Live price (Alpaca)" : e.price_as_of ? `Close on ${e.price_as_of.slice(0, 10)}` : "Last month-end close"}>{usd(e.price)}</span>, className: "num" },
              { key: "years", label: "Total Years", cell: (e) => e.years, className: "num" },
              { key: "count", label: `Total Positive Years`, cell: (e) => e.count, className: "num" },
              { key: "pct", label: `% Positive`, cell: (e) => <span className={`rounded px-1.5 py-0.5 font-semibold bg-bull/10`}>{e.pct === null ? "—" : `${nf(e.pct, 2)}%`}</span>, className: "num" },
              { key: "max", label: `Max Positive Change (%)`, cell: (e) => <span className={tone(e.max)}>{pct(e.max, 2)}</span>, className: "num" },
              { key: "min", label: `Min Positive Change (%)`, cell: (e) => <span className={tone(e.min)}>{pct(e.min, 2)}</span>, className: "num" },
              { key: "avg_dir", label: `Average Positive Change (%)`, cell: (e) => <span className={`font-semibold ${tone(e.avg_dir)}`}>{pct(e.avg_dir, 2)}</span>, className: "num" },
            ]} />
        ) : <Empty>{loading ? "Loading…" : busy ? "Loading price history — results appear when it finishes." : cov?.months ? "No stocks match these filters." : "No price history yet."}</Empty>}
        <p className="mt-2 text-xs text-muted">{data?.note} Max/Min/Average are over the up years only. Monthly moves beyond ±200% are treated as data errors and ignored.</p>
      </Card>

    </div>
  );
}
