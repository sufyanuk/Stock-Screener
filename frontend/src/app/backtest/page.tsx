"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState } from "react";
import { LineChart } from "@/components/charts";
import { Button, Card, Empty, ErrorBox, Field, Stat } from "@/components/ui";
import { api } from "@/lib/api";
import { nf, pct, tone, usd } from "@/lib/format";

export default function BacktestPage() {
  const d = new Date();
  const [p, setP] = useState({
    start: new Date(d.getTime() - 120 * 864e5).toISOString().slice(0, 10), end: d.toISOString().slice(0, 10),
    top_n: "5", max_hold_days: "20", slippage_bps: "10", commission_per_trade: "1", position_value: "10000",
  });
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function run() {
    setBusy(true); setErr(null);
    try {
      const body = { ...p, top_n: Number(p.top_n), max_hold_days: Number(p.max_hold_days), slippage_bps: Number(p.slippage_bps),
        commission_per_trade: Number(p.commission_per_trade), position_value: Number(p.position_value) };
      setRes(await api("/backtest", { method: "POST", body: JSON.stringify(body) }));
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  const m = res?.metrics;
  const fi = (k: keyof typeof p, label: string, type = "text") => <Field label={label}><input type={type} value={p[k]} onChange={(e) => setP({ ...p, [k]: e.target.value })} /></Field>;
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Historical backtest</h1>
      <Card title="Parameters">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-7">
          {fi("start", "Start", "date")}{fi("end", "End", "date")}{fi("top_n", "Top N / day")}{fi("max_hold_days", "Max hold (sessions)")}
          {fi("slippage_bps", "Slippage (bps)")}{fi("commission_per_trade", "Commission ($/side)")}{fi("position_value", "Position size ($)")}
        </div>
        <Button className="mt-3" onClick={run} disabled={busy}>{busy ? "Running…" : "Run backtest"}</Button>
        <p className="mt-2 text-xs text-muted">Point-in-time: each day uses only data available that day (filing dates, XBRL filed dates, prior bars). Entry at next open; stop assumed first if stop and target hit in one bar.</p>
      </Card>
      <ErrorBox error={err} />
      {res && (
        <>
          {!m?.trades ? <Empty>No trades: {res.signals} signals across {res.sessions} sessions. The backtest needs stored options/price history for the period (accumulated by daily scans or historical ingestion).</Empty> : (
            <>
              <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
                <Stat label="Trades" value={m.trades} sub={`${res.signals} signals / ${res.sessions} sessions`} />
                <Stat label="Win rate" value={pct(m.win_rate_pct, 1)} />
                <Stat label="Avg return" value={<span className={tone(m.avg_return_pct)}>{pct(m.avg_return_pct, 2, true)}</span>} />
                <Stat label="Expectancy" value={usd(m.expectancy)} sub={`${nf(m.expectancy_r, 2)} R`} />
                <Stat label="Profit factor" value={nf(m.profit_factor, 2)} />
                <Stat label="Avg win / loss" value={`${usd(m.avg_win)} / ${usd(m.avg_loss)}`} />
                <Stat label="Max drawdown" value={<span className="text-bear">{usd(m.max_drawdown)}</span>} />
                <Stat label="Total P&L" value={<span className={tone(m.total_pnl)}>{usd(m.total_pnl)}</span>} />
                <Stat label="Sharpe (approx.)" value={nf(m.sharpe_trade_based, 2)} sub={m.sharpe_note} />
              </div>
              <Card title="Equity curve"><LineChart points={m.equity_curve.map((x: any, i: number) => ({ time: i + 1, value: x.equity }))} /></Card>
              <Card title="By market regime">
                <table className="text-sm"><thead><tr><th>Regime</th><th>Trades</th><th>Win rate</th><th>Avg return</th></tr></thead>
                  <tbody>{Object.entries(m.by_regime).map(([k, v]: [string, any]) => <tr key={k}><td>{k}</td><td>{v.trades}</td><td>{pct(v.win_rate_pct, 1)}</td><td>{pct(v.avg_return_pct, 2, true)}</td></tr>)}</tbody></table>
              </Card>
              <Card title="Trades">
                <div className="overflow-x-auto"><table className="text-xs"><thead><tr><th>Ticker</th><th>Signal</th><th>Entry</th><th>Exit</th><th>Reason</th><th>P&L</th><th>R</th><th>Score</th></tr></thead>
                  <tbody>{res.trades.map((t: any, i: number) => <tr key={i}><td>{t.ticker}</td><td>{t.signal_date}</td><td>{t.entry_date} @ {usd(t.entry)}</td><td>{t.exit_date} @ {usd(t.exit)}</td>
                    <td>{t.reason}</td><td className={`num ${tone(t.pnl)}`}>{usd(t.pnl)}</td><td className="num">{nf(t.r_multiple, 2)}</td><td className="num">{nf(t.score, 1)}</td></tr>)}</tbody></table></div>
              </Card>
            </>
          )}
          <Card title="Limitations"><ul className="list-disc pl-5 text-sm text-muted">{res.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul></Card>
        </>
      )}
    </div>
  );
}
