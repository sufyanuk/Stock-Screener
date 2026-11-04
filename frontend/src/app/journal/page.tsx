"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState } from "react";
import { LineChart } from "@/components/charts";
import { Button, Card, Empty, ErrorBox, Field, Stat } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { nf, pct, tone, usd } from "@/lib/format";

const today = () => new Date().toISOString().slice(0, 10);

export default function JournalPage() {
  const summary = useApi<any>("/journal/summary");
  const fills = useApi<any[]>("/journal/fills");
  const [f, setF] = useState({ ticker: "", side: "buy", quantity: "", price: "", fees: "0", trade_date: today(), stop: "", target: "", notes: "", recommendation_id: "" });
  const [div, setDiv] = useState({ ticker: "", amount: "", pay_date: today() });
  const [err, setErr] = useState<string | null>(null);
  const [period, setPeriod] = useState("monthly");
  const s = summary.data;

  async function addFill() {
    setErr(null);
    try {
      const body: any = { ...f, quantity: Number(f.quantity), price: Number(f.price), fees: Number(f.fees) };
      for (const k of ["stop", "target", "recommendation_id"]) body[k] = body[k] === "" ? null : Number(body[k]);
      await api("/journal/fills", { method: "POST", body: JSON.stringify(body) });
      setF({ ...f, quantity: "", price: "", notes: "" });
      summary.reload(); fills.reload();
    } catch (e) { setErr((e as Error).message); }
  }
  async function addDiv() {
    setErr(null);
    try {
      await api("/journal/dividends", { method: "POST", body: JSON.stringify({ ...div, amount: Number(div.amount) }) });
      setDiv({ ...div, amount: "" }); summary.reload();
    } catch (e) { setErr((e as Error).message); }
  }
  async function del(id: number) {
    await api(`/journal/fills/${id}`, { method: "DELETE" });
    summary.reload(); fills.reload();
  }
  const fi = (k: keyof typeof f, label: string, type = "text") => <Field label={label}><input type={type} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></Field>;

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Trading journal & P&amp;L</h1>
      <ErrorBox error={err || summary.error} />
      {s && (
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
          <Stat label="Total P&L" value={<span className={tone(s.total_pnl)}>{usd(s.total_pnl)}</span>} />
          <Stat label="Realized" value={<span className={tone(s.realized_pnl)}>{usd(s.realized_pnl)}</span>} />
          <Stat label="Unrealized" value={<span className={tone(s.unrealized_pnl)}>{usd(s.unrealized_pnl)}</span>} sub={s.mark_source} />
          <Stat label="Dividends" value={usd(s.dividends)} />
          <Stat label="Fees" value={usd(s.total_fees)} />
          <Stat label="Exposure" value={usd(s.exposure)} />
          <Stat label="Win rate" value={pct(s.win_rate_pct, 1)} sub={`avg win ${usd(s.avg_win)} / loss ${usd(s.avg_loss)}`} />
          <Stat label="Max drawdown" value={<span className="text-bear">{usd(s.max_drawdown)}</span>} />
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Equity curve (realized, USD)">{s?.equity_curve.length ? <LineChart points={s.equity_curve.map((p: any) => ({ time: p.date, value: p.equity }))} /> : <Empty>No closed trades yet.</Empty>}</Card>
        <Card title="Drawdown">{s?.drawdown_curve.length ? <LineChart color="#ef4444" points={s.drawdown_curve.map((p: any) => ({ time: p.date, value: p.drawdown }))} /> : <Empty>No closed trades yet.</Empty>}</Card>
      </div>
      <Card title="Record a fill">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          {fi("ticker", "Ticker")}
          <Field label="Side"><select value={f.side} onChange={(e) => setF({ ...f, side: e.target.value })}><option value="buy">Buy</option><option value="sell">Sell / short</option></select></Field>
          {fi("quantity", "Quantity")}{fi("price", "Price ($)")}{fi("fees", "Fees ($)")}{fi("trade_date", "Date", "date")}{fi("stop", "Stop ($)")}{fi("target", "Target ($)")}
          {fi("recommendation_id", "Recommendation ID")}{fi("notes", "Notes")}
        </div>
        <Button className="mt-3" onClick={addFill}>Add fill</Button>
        <p className="mt-2 text-xs text-muted">Sells without an open long open a short; positions are matched FIFO. Link a recommendation ID to compare actual vs. recommended.</p>
      </Card>
      <Card title="Record a dividend">
        <div className="grid grid-cols-3 gap-3">
          <Field label="Ticker"><input value={div.ticker} onChange={(e) => setDiv({ ...div, ticker: e.target.value })} /></Field>
          <Field label="Amount ($)"><input value={div.amount} onChange={(e) => setDiv({ ...div, amount: e.target.value })} /></Field>
          <Field label="Pay date"><input type="date" value={div.pay_date} onChange={(e) => setDiv({ ...div, pay_date: e.target.value })} /></Field>
        </div>
        <Button className="mt-3" onClick={addDiv}>Add dividend</Button>
      </Card>
      <Card title="Open positions">
        {s?.open_positions.length ? <table className="text-sm"><thead><tr><th>Ticker</th><th>Qty</th><th>Avg</th><th>Mark</th><th>Unrealized</th><th>Opened</th></tr></thead>
          <tbody>{s.open_positions.map((p: any) => <tr key={p.ticker}><td>{p.ticker}</td><td className="num">{nf(p.quantity, 2)}</td><td className="num">{usd(p.avg_price)}</td>
            <td className="num">{p.mark ? usd(p.mark) : <span className="text-warn">no price</span>}</td><td className={`num ${tone(p.unrealized)}`}>{usd(p.unrealized)}</td><td>{p.opened}</td></tr>)}</tbody></table> : <Empty>No open positions.</Empty>}
      </Card>
      <Card title="Closed trades">
        {s?.closed_trades.length ? <div className="overflow-x-auto"><table className="text-sm"><thead><tr><th>Ticker</th><th>Dir</th><th>Qty</th><th>Entry</th><th>Exit</th><th>Dates</th><th>Fees</th><th>P&L</th><th>Return</th><th>vs. recommendation</th></tr></thead>
          <tbody>{s.closed_trades.map((c: any, i: number) => <tr key={i}><td>{c.ticker}</td><td>{c.direction}</td><td className="num">{nf(c.quantity, 2)}</td><td className="num">{usd(c.entry_price)}</td>
            <td className="num">{usd(c.exit_price)}</td><td>{c.entry_date} → {c.exit_date}</td><td className="num">{usd(c.fees)}</td><td className={`num ${tone(c.pnl)}`}>{usd(c.pnl)}</td>
            <td className={`num ${tone(c.return_pct)}`}>{pct(c.return_pct, 2, true)}</td>
            <td className="text-xs text-muted">{c.recommended ? `rec ${c.recommended.session_date}: entry ${usd(c.recommended.entry)}, stop ${usd(c.recommended.stop)}, T1 ${usd(c.recommended.target1)}` : "—"}</td></tr>)}</tbody></table></div> : <Empty>No closed trades.</Empty>}
      </Card>
      <Card title="Performance by period" right={<select value={period} onChange={(e) => setPeriod(e.target.value)}>{["daily", "weekly", "monthly", "quarterly", "yearly"].map((p) => <option key={p}>{p}</option>)}</select>}>
        {s?.periods?.[period]?.length ? <table className="text-sm"><thead><tr><th>Period</th><th>Realized P&L</th></tr></thead>
          <tbody>{s.periods[period].map((p: any) => <tr key={p.period}><td>{p.period}</td><td className={`num ${tone(p.pnl)}`}>{usd(p.pnl)}</td></tr>)}</tbody></table> : <Empty>No data.</Empty>}
      </Card>
      <Card title="All fills">
        {fills.data?.length ? <table className="text-sm"><thead><tr><th>Date</th><th>Ticker</th><th>Side</th><th>Qty</th><th>Price</th><th>Fees</th><th></th></tr></thead>
          <tbody>{fills.data.map((x) => <tr key={x.id}><td>{x.trade_date}</td><td>{x.ticker}</td><td>{x.side}</td><td className="num">{x.quantity}</td><td className="num">{usd(x.price)}</td><td className="num">{usd(x.fees)}</td>
            <td><Button variant="danger" onClick={() => del(x.id)}>Delete</Button></td></tr>)}</tbody></table> : <Empty>No fills.</Empty>}
      </Card>
    </div>
  );
}
