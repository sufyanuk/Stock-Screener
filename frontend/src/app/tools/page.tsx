"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useState } from "react";
import { LineChart } from "@/components/charts";
import { Button, Card, ErrorBox, Field, Stat } from "@/components/ui";
import { api } from "@/lib/api";
import { nf, pct, usd } from "@/lib/format";

function num(v: string) {
  return v === "" ? null : Number(v);
}

function Position() {
  const [f, setF] = useState({
    account_balance: "25000", risk_pct: "1", entry: "", stop: "", target1: "", target2: "",
    max_allocation_pct: "20", available_capital: "", commission_per_trade: "0", commission_per_share: "0", slippage_per_share: "0.01",
    direction: "long", fractional: false,
  });
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    // Prefill from a stock page link (?entry=&stop=&t1=&t2=); read client-side because the page is prerendered.
    const q = new URLSearchParams(window.location.search);
    if (!q.get("entry")) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time prefill from the URL
    setF((x) => ({ ...x, entry: q.get("entry") ?? "", stop: q.get("stop") ?? "", target1: q.get("t1") ?? "", target2: q.get("t2") ?? "" }));
  }, []);
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setF({ ...f, [k]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value });
  async function calc() {
    setErr(null);
    try {
      const body: any = { ...f };
      for (const k of Object.keys(body)) if (typeof body[k] === "string" && k !== "direction") body[k] = num(body[k]);
      for (const k of ["target1", "target2", "available_capital"]) if (body[k] === null) delete body[k];
      setRes(await api("/tools/position", { method: "POST", body: JSON.stringify(body) }));
    } catch (e) {
      setErr((e as Error).message);
      setRes(null);
    }
  }
  return (
    <Card title="Position size calculator">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Field label="Account balance ($)"><input value={f.account_balance} onChange={set("account_balance")} inputMode="decimal" /></Field>
        <Field label="Risk per trade (%)"><input value={f.risk_pct} onChange={set("risk_pct")} inputMode="decimal" /></Field>
        <Field label="Direction"><select value={f.direction} onChange={set("direction")}><option value="long">Long</option><option value="short">Short</option></select></Field>
        <Field label="Max allocation (% of account)"><input value={f.max_allocation_pct} onChange={set("max_allocation_pct")} /></Field>
        <Field label="Entry ($)"><input value={f.entry} onChange={set("entry")} /></Field>
        <Field label="Stop-loss ($)"><input value={f.stop} onChange={set("stop")} /></Field>
        <Field label="Target 1 ($)"><input value={f.target1} onChange={set("target1")} /></Field>
        <Field label="Target 2 ($)"><input value={f.target2} onChange={set("target2")} /></Field>
        <Field label="Available capital ($)" hint="blank = account balance"><input value={f.available_capital} onChange={set("available_capital")} /></Field>
        <Field label="Commission per trade ($)"><input value={f.commission_per_trade} onChange={set("commission_per_trade")} /></Field>
        <Field label="Commission per share ($)"><input value={f.commission_per_share} onChange={set("commission_per_share")} /></Field>
        <Field label="Slippage per share ($)"><input value={f.slippage_per_share} onChange={set("slippage_per_share")} /></Field>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.fractional} onChange={set("fractional")} /> Fractional shares</label>
      </div>
      <Button className="mt-3" onClick={calc}>Calculate</Button>
      <div className="mt-3"><ErrorBox error={err} /></div>
      {res && (
        <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
          <Stat label="Shares" value={nf(res.shares, f.fractional ? 4 : 0)} sub={`limited by ${res.limited_by}`} />
          <Stat label="Investment" value={usd(res.investment)} sub={`capital req. ${usd(res.capital_required)}`} />
          <Stat label="Max loss" value={<span className="text-bear">{usd(res.max_loss)}</span>} sub={`risk budget ${usd(res.risk_amount)}`} />
          <Stat label="% of account" value={pct(res.pct_of_account, 2)} sub={`cash left ${usd(res.remaining_cash)}`} />
          <Stat label="Profit @ T1" value={<span className="text-bull">{usd(res.profit_target1)}</span>} sub={res.rr_target1 ? `${res.rr_target1}R` : ""} />
          <Stat label="Profit @ T2" value={<span className="text-bull">{usd(res.profit_target2)}</span>} sub={res.rr_target2 ? `${res.rr_target2}R` : ""} />
          <Stat label="Risk per share" value={usd(res.risk_per_share, 4)} sub="incl. slippage & per-share fees" />
          <Stat label="Effective fills" value={`${usd(res.effective_entry)} / ${usd(res.effective_stop)}`} sub="entry / stop" />
        </div>
      )}
      <p className="mt-3 text-xs text-muted">Risk amount = balance × risk %. Risk/share = |entry − stop| + slippage both ways + per-share commission both ways. Shares = min(risk-based, allocation-based).</p>
    </Card>
  );
}

function Expectancy() {
  const [f, setF] = useState({ win_rate_pct: "45", avg_win: "300", avg_loss: "150", num_trades: "100", risk_per_trade: "150", cost_per_trade: "2", starting_capital: "10000", ruin_threshold_pct: "50" });
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  async function calc() {
    setErr(null);
    try {
      const body = Object.fromEntries(Object.entries(f).map(([k, v]) => [k, Number(v)]));
      setRes(await api("/tools/expectancy", { method: "POST", body: JSON.stringify(body) }));
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  const field = (k: keyof typeof f, label: string) => <Field label={label}><input value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></Field>;
  const wr = [30, 40, 50, 60, 70];
  const rrs = [1, 1.5, 2, 3, 4];
  return (
    <Card title="Trade expectancy calculator">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {field("win_rate_pct", "Win rate (%)")}{field("avg_win", "Average win ($)")}{field("avg_loss", "Average loss ($)")}{field("num_trades", "Number of trades")}
        {field("risk_per_trade", "Risk per trade ($, 1R)")}{field("cost_per_trade", "Costs per trade ($)")}{field("starting_capital", "Starting capital ($)")}{field("ruin_threshold_pct", "Ruin = drawdown of (%)")}
      </div>
      <Button className="mt-3" onClick={calc}>Calculate</Button>
      <div className="mt-3"><ErrorBox error={err} /></div>
      {res && (
        <>
          <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
            <Stat label="Expectancy / trade" value={<span className={res.expectancy_per_trade >= 0 ? "text-bull" : "text-bear"}>{usd(res.expectancy_per_trade)}</span>} sub={`${nf(res.expectancy_r, 3)} R`} />
            <Stat label={`Expected over ${f.num_trades} trades`} value={usd(res.expected_total)} />
            <Stat label="Profit factor" value={nf(res.profit_factor, 2)} sub={`payoff ${nf(res.payoff_ratio, 2)}`} />
            <Stat label="Break-even win rate" value={pct(res.break_even_win_rate_pct, 1)} />
            <Stat label="Risk of ruin" value={pct(res.risk_of_ruin_pct, 2)} sub="Monte Carlo" />
            <Stat label="Max drawdown (median)" value={usd(res.simulated_max_drawdown.median)} sub={`95th pct ${usd(res.simulated_max_drawdown.p95)}`} />
          </div>
          <div className="mt-4"><div className="mb-1 text-xs text-muted">Simulated equity: median with 5th/95th percentile bands</div>
            <LineChart points={res.equity_bands.map((b: any) => ({ time: b.trade, value: b.p50 }))} bands={res.equity_bands.map((b: any) => ({ time: b.trade, p5: b.p5, p95: b.p95 }))} /></div>
          <div className="mt-4 overflow-x-auto"><div className="mb-1 text-xs text-muted">Scenario grid: expectancy per trade at 1R = {usd(Number(f.avg_loss))}</div>
            <table className="text-xs"><thead><tr><th>Win rate \ R:R</th>{rrs.map((r) => <th key={r}>{r}:1</th>)}</tr></thead>
              <tbody>{wr.map((w) => (<tr key={w}><td>{w}%</td>{rrs.map((r) => {
                const s = res.scenarios.find((x: any) => x.win_rate_pct === w && x.rr === r);
                return <td key={r} className={`num ${s.expectancy > 0 ? "text-bull" : s.expectancy < 0 ? "text-bear" : ""}`}>{usd(s.expectancy)}</td>;
              })}</tr>))}</tbody></table></div>
          <p className="mt-3 text-xs text-muted">Assumptions: {Object.entries(res.assumptions).filter(([, v]) => v).map(([k, v]) => `${k}: ${v}`).join(" · ")}. {res.disclaimer}</p>
        </>
      )}
    </Card>
  );
}

export default function ToolsPage() {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Calculators</h1>
      <Position />
      <Expectancy />
    </div>
  );
}
