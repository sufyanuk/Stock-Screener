"use client";
import { useEffect, useRef, useState } from "react";
import { Button, Card, Empty, ErrorBox, Field, ShariahLegend, Ticker, useSort } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { compact, nf, pct, tone, usd } from "@/lib/format";

type Kind = "low52" | "ema50";
type Row = {
  symbol: string; date: string; price: number; day_change_pct: number | null; volume: number; vol_ratio: number | null;
  avg_vol50: number | null; avg_vol20: number | null; low52: number | null; low52_date: string | null; above_low52_pct: number | null;
  ema50: number | null; vs_ema50_pct: number | null; low_vs_ema_pct: number | null; rsi: number | null; shariah_status: string | null;
};
type Status = { sessions: number; first: string | null; as_of: string | null; target: string; remaining: number; stale: number; universe: number;
  symbols: number; provider: string | null; age_days: number | null };
type Resp = { rows: Row[]; total: number; data: Status };

const num = (v: string) => v.replace(/[^0-9.]/g, "");

export default function BouncesPage() {
  const [kind, setKind] = useState<Kind>("low52");
  const [maxPrice, setMaxPrice] = useState("100");
  const [nearLow, setNearLow] = useState("5");
  const [recover, setRecover] = useState("2");
  const [touch, setTouch] = useState("1");
  const [volMult, setVolMult] = useState("1.5");
  const [useRsi, setUseRsi] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const qs = new URLSearchParams({ kind, max_price: maxPrice || "100", vol_mult: volMult || "1.5", use_rsi: String(useRsi) });
  if (kind === "low52") { qs.set("near_low_pct", nearLow || "5"); qs.set("min_recover_pct", recover || "2"); }
  else qs.set("touch_pct", touch || "1");
  const { data, error, reload, loading } = useApi<Resp>(`/bounces?${qs}`);
  const { sorted, th } = useSort(data?.rows ?? [], null);
  const info = data?.data;

  async function load(rebuild = false) {
    setErr(null);
    setBusy("Loading daily prices for all US stocks…");
    try {
      for (let i = 0; i < 40; i++) {
        const r = await api<Status & { processed: number; errors: string[] }>("/bounces/refresh", {
          method: "POST", body: JSON.stringify({ max_seconds: 200, rebuild: rebuild && i === 0 }) });
        setBusy(`Loaded ${(r.universe - r.remaining).toLocaleString()} of ${r.universe.toLocaleString()} stocks${r.stale ? ` · ${r.stale.toLocaleString()} to update` : ""}…`);
        if (r.errors.length) setErr(r.errors[0]);
        if ((r.remaining <= 0 && r.stale <= 0) || r.processed === 0) break;
      }
    } catch (e) {
      setErr((e as Error).message);
    }
    setBusy(null);
    reload();
  }

  const loaded = !!info?.sessions;
  // Load automatically when prices are missing or behind the latest session (once per page visit).
  const auto = useRef(false);
  useEffect(() => {
    if (!info || auto.current) return;
    if (info.remaining > 0 || info.stale > 0 || !info.symbols) {
      auto.current = true;
      // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time automatic load when prices are missing or stale
      load();
    }
  }, [info]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Bounces</h1>
          <p className="text-sm text-muted">
            US stocks bouncing off their 52-week low or their 50-day EMA, from a year of daily prices (Alpaca, consolidated SIP feed).
            {loaded && <> {info!.symbols.toLocaleString()} stocks · prices as of {info!.as_of}</>}
          </p>
        </div>
        <ShariahLegend />
      </div>

      <Card>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          {!loaded || (info && (info.remaining > 0 || info.stale > 0)) ? (
            <Button onClick={() => load()} disabled={!!busy}>{loaded ? `Load remaining ${(info!.remaining + info!.stale).toLocaleString()} stocks` : "Load price history (all US stocks, about 2 minutes)"}</Button>
          ) : (
            <>
              <span className="text-muted">Prices as of <b className="text-ink">{info!.as_of}</b> · updated by each scan.</span>
              <Button variant="ghost" onClick={() => load()} disabled={!!busy}>Update now</Button>
              <Button variant="ghost" onClick={() => { if (confirm("Reload the full year of daily prices for every stock?")) load(true); }} disabled={!!busy}>Rebuild</Button>
            </>
          )}
          {busy && <span className="text-xs text-muted">{busy}</span>}
        </div>
        {info?.age_days != null && info.age_days > 3 && <p className="mt-2 text-sm text-warn">Prices are {info.age_days} days behind the latest session ({info.target}) — click “Update now”.</p>}
        <div className="mt-2"><ErrorBox error={err || error} /></div>
      </Card>

      <Card>
        <div className="mb-3 flex gap-1">
          {([["low52", "Bouncing from 52-week low"], ["ema50", "Bouncing from 50 EMA"]] as const).map(([k, label]) => (
            <button key={k} onClick={() => setKind(k)}
              className={`rounded-md px-3 py-1.5 text-sm ${kind === k ? "bg-accent text-bg" : "border border-line text-muted hover:text-ink"}`}>{label}</button>
          ))}
        </div>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Field label="Price below ($)"><input value={maxPrice} onChange={(e) => setMaxPrice(num(e.target.value))} /></Field>
          {kind === "low52" ? (
            <>
              <Field label="Within % of 52-week low"><input value={nearLow} onChange={(e) => setNearLow(num(e.target.value))} /></Field>
              <Field label="Recovered at least (%)"><input value={recover} onChange={(e) => setRecover(num(e.target.value))} /></Field>
              <Field label="Volume > × 50-day average"><input value={volMult} onChange={(e) => setVolMult(num(e.target.value))} /></Field>
            </>
          ) : (
            <>
              <Field label="Low within % of 50 EMA"><input value={touch} onChange={(e) => setTouch(num(e.target.value))} /></Field>
              <Field label="Volume ≥ × 20-day average"><input value={volMult} onChange={(e) => setVolMult(num(e.target.value))} /></Field>
            </>
          )}
          <label className="flex items-end gap-1.5 pb-2 text-sm"><input type="checkbox" checked={useRsi} onChange={(e) => setUseRsi(e.target.checked)} />
            RSI {kind === "low52" ? "30–50" : "40–60"} (optional)</label>
        </div>
      </Card>

      <Card title={data ? `${data.total.toLocaleString()} stock${data.total === 1 ? "" : "s"} ${kind === "low52" ? "bouncing from a 52-week low" : "bouncing from the 50 EMA"}${info?.as_of ? ` · ${info.as_of}` : ""}` : "Loading…"}>
        {loading && !data ? <Empty>Loading…</Empty> : sorted.length ? (
          <div className="overflow-x-auto">
            <table className="text-sm">
              <thead><tr>
                {th("symbol", "Ticker")}{th("price", "Price")}{th("day_change_pct", "Day")}
                {kind === "low52"
                  ? <>{th("low52", "52w low")}{th("low52_date", "Low date")}{th("above_low52_pct", "Above low")}</>
                  : <>{th("ema50", "50 EMA")}{th("low_vs_ema_pct", "Day low vs EMA")}{th("vs_ema50_pct", "Close vs EMA")}</>}
                {th("volume", "Volume")}{th("vol_ratio", kind === "low52" ? "× 50d avg" : "× 20d avg")}{th("rsi", "RSI 14")}
              </tr></thead>
              <tbody>{sorted.map((r) => (
                <tr key={r.symbol}>
                  <td><Ticker symbol={r.symbol} status={r.shariah_status} /></td>
                  <td className="num">{usd(r.price)}</td>
                  <td className={`num ${tone(r.day_change_pct)}`}>{pct(r.day_change_pct, 2, true)}</td>
                  {kind === "low52" ? <>
                    <td className="num">{usd(r.low52)}</td><td className="text-xs text-muted">{r.low52_date}</td>
                    <td className="num text-bull">{pct(r.above_low52_pct, 1, true)}</td>
                  </> : <>
                    <td className="num">{usd(r.ema50)}</td><td className="num">{pct(r.low_vs_ema_pct, 2, true)}</td>
                    <td className="num text-bull">{pct(r.vs_ema50_pct, 2, true)}</td>
                  </>}
                  <td className="num">{compact(r.volume)}</td>
                  <td className="num font-semibold">{nf(r.vol_ratio, 1)}×</td>
                  <td className="num">{nf(r.rsi, 0)}</td>
                </tr>))}</tbody>
            </table>
          </div>
        ) : <Empty>{loaded ? "No stocks match these criteria today." : "No price history yet — click “Load price history”."}</Empty>}
        <p className="mt-2 text-xs text-muted">
          {kind === "low52"
            ? "52-week low = lowest daily low of the last 252 sessions. Criteria: price below the cap, close within the chosen % of the 52-week low and recovered at least the chosen % from it, volume above the multiple of the previous 50-day average volume, optional RSI(14) 30–50."
            : "50 EMA = 50-day exponential moving average of closes. Criteria: price below the cap, the day's low came within the chosen % of the EMA, the close is above the EMA, volume at least the multiple of the previous 20-day average, optional RSI(14) 40–60."}
          {" "}Latest completed session only. Research only — a bounce can fail.
        </p>
      </Card>
    </div>
  );
}
