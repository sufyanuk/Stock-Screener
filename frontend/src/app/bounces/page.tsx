"use client";
import { useState } from "react";
import { PriceImport } from "@/components/price-import";
import { Card, Empty, ErrorBox, Field, ShariahLegend, Ticker, useSort } from "@/components/ui";
import { useApi } from "@/lib/api";
import { compact, pct, tone, usd } from "@/lib/format";

type Kind = "low52" | "ema50";
type Row = {
  symbol: string; date: string; price: number; day_change_pct: number | null; avg_vol50: number | null; low52: number | null; low52_date: string | null;
  high52: number | null; below_high52_pct: number | null; stale: boolean; shariah_status: string | null;
  bounce_pct?: number; sessions_since_low?: number; ema50?: number; above_ema_pct?: number; sessions_since_touch?: number; ema_change_10d_pct?: number;
};
type Resp = { rows: Row[]; total: number; data: { symbols: number; as_of: string | null; age_days: number | null; source: string | null; imported_at: string | null } };

const num = (v: string) => v.replace(/[^0-9.]/g, "");

export default function BouncesPage() {
  const [kind, setKind] = useState<Kind>("low52");
  const [minPrice, setMinPrice] = useState("5");
  const [maxPrice, setMaxPrice] = useState("100");
  const [minVol, setMinVol] = useState("200000");
  const [upDay, setUpDay] = useState(true);
  const [lowDays, setLowDays] = useState("10");
  const [minBounce, setMinBounce] = useState("3");
  const [maxBounce, setMaxBounce] = useState("20");
  const [emaDays, setEmaDays] = useState("3");
  const [touch, setTouch] = useState("1");
  const [maxAbove, setMaxAbove] = useState("5");
  const [rising, setRising] = useState(true);

  const qs = new URLSearchParams({ kind, up_day: String(upDay), min_avg_volume: minVol || "0" });
  qs.set("min_price", minPrice || "0");
  if (maxPrice) qs.set("max_price", maxPrice);
  if (kind === "low52") {
    qs.set("within_days", lowDays || "10"); qs.set("min_bounce_pct", minBounce || "0"); qs.set("max_bounce_pct", maxBounce || "1000");
  } else {
    qs.set("within_days", emaDays || "3"); qs.set("touch_pct", touch || "0"); qs.set("max_above_pct", maxAbove || "100"); qs.set("require_rising", String(rising));
  }
  const { data, error, reload, loading } = useApi<Resp>(`/bounces?${qs}`);
  const { sorted, th } = useSort(data?.rows ?? [], null); // server order: most recent bounce first
  const info = data?.data;
  const ageDays = info?.age_days ?? null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Bounces</h1>
          <p className="text-sm text-muted">
            Stocks turning up from their 52-week low or from a rising 50-day EMA, across every stock in your price CSV.
            {info?.symbols ? <> Data: {info.symbols.toLocaleString()} stocks · prices as of {info.as_of} · {info.source}</> : null}
          </p>
        </div>
        <ShariahLegend />
      </div>
      {ageDays !== null && ageDays > 4 && (
        <div className="rounded-lg border border-warn/40 bg-warn/10 p-3 text-sm text-warn">
          Prices are from {info?.as_of} ({ageDays} days ago). Import a current price CSV below to refresh the bounce lists.
        </div>
      )}
      {(data || error) && <PriceImport open={!info?.symbols} onDone={reload} />}

      <Card>
        <div className="mb-3 flex gap-1">
          {([["low52", "Bouncing from 52-week low"], ["ema50", "Bouncing from 50 EMA"]] as const).map(([k, label]) => (
            <button key={k} onClick={() => setKind(k)}
              className={`rounded-md px-3 py-1.5 text-sm ${kind === k ? "bg-accent text-bg" : "border border-line text-muted hover:text-ink"}`}>{label}</button>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
          <Field label="Price from ($)"><input value={minPrice} onChange={(e) => setMinPrice(num(e.target.value))} placeholder="any" /></Field>
          <Field label="Price below ($)"><input value={maxPrice} onChange={(e) => setMaxPrice(num(e.target.value))} placeholder="any" /></Field>
          <Field label="Min avg volume (50d)"><input value={minVol} onChange={(e) => setMinVol(num(e.target.value))} placeholder="0" /></Field>
          {kind === "low52" ? (
            <>
              <Field label="Low set within (sessions)"><input value={lowDays} onChange={(e) => setLowDays(num(e.target.value))} /></Field>
              <Field label="Bounce at least (%)"><input value={minBounce} onChange={(e) => setMinBounce(num(e.target.value))} /></Field>
              <Field label="Bounce at most (%)"><input value={maxBounce} onChange={(e) => setMaxBounce(num(e.target.value))} /></Field>
            </>
          ) : (
            <>
              <Field label="Touched EMA within (sessions)"><input value={emaDays} onChange={(e) => setEmaDays(num(e.target.value))} /></Field>
              <Field label="Touch tolerance (%)"><input value={touch} onChange={(e) => setTouch(num(e.target.value))} /></Field>
              <Field label="Max above EMA (%)"><input value={maxAbove} onChange={(e) => setMaxAbove(num(e.target.value))} /></Field>
              <label className="flex items-end gap-1.5 pb-2 text-sm"><input type="checkbox" checked={rising} onChange={(e) => setRising(e.target.checked)} /> EMA rising</label>
            </>
          )}
          <label className="flex items-end gap-1.5 pb-2 text-sm"><input type="checkbox" checked={upDay} onChange={(e) => setUpDay(e.target.checked)} /> Closed up on the day</label>
        </div>
      </Card>

      <ErrorBox error={error} />
      <Card title={data ? `${data.total.toLocaleString()} stock${data.total === 1 ? "" : "s"} ${kind === "low52" ? "bouncing from a 52-week low" : "bouncing from the 50 EMA"}` : "Loading…"}>
        {loading && !data ? <Empty>Loading…</Empty> : sorted.length ? (
          <div className="overflow-x-auto">
            <table className="text-sm">
              <thead><tr>
                {th("symbol", "Ticker")}{th("price", "Price")}{th("day_change_pct", "Day")}
                {kind === "low52" ? <>{th("low52", "52w low")}<th>Low date</th>{th("sessions_since_low", "Sessions since low")}{th("bounce_pct", "Bounce off low")}</>
                  : <>{th("ema50", "50 EMA")}{th("above_ema_pct", "Above EMA")}{th("sessions_since_touch", "Sessions since touch")}{th("ema_change_10d_pct", "EMA 10d slope")}</>}
                {th("below_high52_pct", "From 52w high")}{th("avg_vol50", "Avg volume")}
              </tr></thead>
              <tbody>{sorted.map((r) => (
                <tr key={r.symbol} className={r.stale ? "opacity-60" : ""}>
                  <td><Ticker symbol={r.symbol} status={r.shariah_status} />{r.stale && <span className="ml-1 text-[10px] text-warn" title={`last bar ${r.date}`}>old</span>}</td>
                  <td className="num">{usd(r.price)}</td>
                  <td className={`num ${tone(r.day_change_pct)}`}>{pct(r.day_change_pct, 2, true)}</td>
                  {kind === "low52" ? <>
                    <td className="num">{usd(r.low52)}</td><td className="text-xs text-muted">{r.low52_date}</td>
                    <td className="num">{r.sessions_since_low}</td><td className="num text-bull">{pct(r.bounce_pct, 1, true)}</td>
                  </> : <>
                    <td className="num">{usd(r.ema50)}</td><td className="num text-bull">{pct(r.above_ema_pct, 1, true)}</td>
                    <td className="num">{r.sessions_since_touch === 0 ? "today" : r.sessions_since_touch}</td>
                    <td className={`num ${tone(r.ema_change_10d_pct)}`}>{pct(r.ema_change_10d_pct, 1, true)}</td>
                  </>}
                  <td className={`num ${tone(r.below_high52_pct)}`}>{pct(r.below_high52_pct, 1, true)}</td>
                  <td className="num">{compact(r.avg_vol50)}</td>
                </tr>))}</tbody>
            </table>
          </div>
        ) : <Empty>{info?.symbols ? "No stocks match these settings." : "No price data yet — import your OHLCV CSV above (include at least the last 14 months)."}</Empty>}
        <p className="mt-2 text-xs text-muted">
          {kind === "low52"
            ? "52-week low = lowest daily low of the last 252 sessions (stocks with fewer than 200 sessions are skipped). A bounce = the low was set within the chosen number of sessions (not today) and the latest close is the chosen % above it."
            : "50 EMA = 50-day exponential moving average of closes. A bounce = a daily low came within the tolerance of the EMA in the chosen window, no close in the last 10 sessions fell more than the tolerance below it, and the latest close is above the EMA."}
          {" "}Research only — a bounce can fail.
        </p>
      </Card>
    </div>
  );
}
