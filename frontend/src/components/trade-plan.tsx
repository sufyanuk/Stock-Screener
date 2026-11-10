"use client";
/** Stop loss, Target 1 and position size for the stock in the side panel. */
import { useState } from "react";
import { nf, pct, usd } from "@/lib/format";

export type TradePlan = { stop_loss: number; target1: number | null; entry: number; as_of: string; method: string | null } | null;

const num = (v: string) => v.replace(/[^0-9.]/g, "");

function useStored(key: string, initial: string) {
  // the panel only renders in the browser, after its data has loaded
  const [v, setV] = useState(() => {
    try { return (typeof window !== "undefined" && localStorage.getItem(key)) || initial; } catch { return initial; }
  });
  const set = (x: string) => { setV(x); try { localStorage.setItem(key, x); } catch { /* storage unavailable */ } };
  return [v, set] as const;
}

/** Render with key={ticker} so a new stock starts again from its own plan. */
export function TradePlanCard({ price, plan }: { price: number | null; plan: TradePlan }) {
  const entry = price ?? plan?.entry ?? null;
  const [stop, setStop] = useState(plan ? plan.stop_loss.toFixed(2) : "");
  const [target, setTarget] = useState(plan?.target1 ? plan.target1.toFixed(2) : "");
  const [account, setAccount] = useStored("trade-account-size", "10000");
  const [riskPct, setRiskPct] = useStored("trade-risk-pct", "1");

  const s = Number(stop) || null, t = Number(target) || null, acc = Number(account) || 0, rp = Number(riskPct) || 0;
  const riskPerShare = entry && s && s < entry ? entry - s : null;
  const riskAmount = acc * rp / 100;
  const byRisk = riskPerShare ? Math.floor(riskAmount / riskPerShare) : 0;
  const byCash = entry ? Math.floor(acc / entry) : 0;  // never more than the account can buy
  const shares = riskPerShare ? Math.max(0, Math.min(byRisk, byCash)) : 0;
  const rr = riskPerShare && t && entry && t > entry ? (t - entry) / riskPerShare : null;
  const field = "w-full rounded-md border border-line bg-bg px-2 py-1 text-sm num";

  return (
    <div className="rounded-lg border border-line bg-panel-2 p-3">
      <div className="text-[11px] uppercase tracking-wide text-muted">Trade plan</div>
      <div className="mt-2 grid grid-cols-2 gap-2">
        <label className="text-xs text-muted">Stop loss ($)
          <input className={field} value={stop} onChange={(e) => setStop(num(e.target.value))} placeholder="—" />
          {s && entry ? <span className="text-bear">{pct((s - entry) / entry * 100, 1, true)} from price</span> : null}
        </label>
        <label className="text-xs text-muted">Target 1 ($)
          <input className={field} value={target} onChange={(e) => setTarget(num(e.target.value))} placeholder="—" />
          {t && entry ? <span className="text-bull">{pct((t - entry) / entry * 100, 1, true)} from price</span> : null}
        </label>
      </div>
      <p className="mt-1 text-[11px] text-muted">
        {plan ? <>From the daily chart to {plan.as_of}: stop just below the nearest support zone, Target 1 = next resistance zone{plan.target1 ? "" : " (none found above the price)"}. You can change both.</>
          : <>No support zone below the price (or too little history), so no stop is suggested — enter your own.</>}
        {rr !== null && <> Reward : risk to Target 1 = <b className="text-ink">{nf(rr, 2)} : 1</b>.</>}
      </p>

      <div className="mt-3 text-[11px] uppercase tracking-wide text-muted">Size this trade</div>
      <div className="mt-1 grid grid-cols-2 gap-2">
        <label className="text-xs text-muted">Account size ($)
          <input className={field} value={account} onChange={(e) => setAccount(num(e.target.value))} />
        </label>
        <label className="text-xs text-muted">Risk per trade (%)
          <input className={field} value={riskPct} onChange={(e) => setRiskPct(num(e.target.value))} />
        </label>
      </div>
      {riskPerShare && entry ? (
        <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-sm">
          <span className="text-muted">Buy</span><span className="num text-right font-semibold">{shares.toLocaleString()} shares</span>
          <span className="text-muted">Position value</span><span className="num text-right">{usd(shares * entry)}</span>
          <span className="text-muted">Loss at stop</span><span className="num text-right text-bear">−{usd(shares * riskPerShare)}</span>
          {t && t > entry ? <><span className="text-muted">Profit at Target 1</span><span className="num text-right text-bull">+{usd(shares * (t - entry))}</span></> : null}
          {byRisk > byCash && <span className="col-span-2 text-[11px] text-warn">Capped by account size: the full risk budget would need more money than the account holds.</span>}
        </div>
      ) : <p className="mt-2 text-xs text-muted">{entry ? "Enter a stop loss below the price to size the trade." : "No current price to size the trade."}</p>}
      <p className="mt-1 text-[11px] text-muted">At the current price of {usd(entry)}; risking {usd(riskAmount)} ({nf(rp, 2)}% of the account). Fees and slippage not included — see Calculators for those.</p>
    </div>
  );
}
