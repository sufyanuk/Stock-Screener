"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
/** Screening criteria editors, shown on the tab each criterion belongs to (there is no separate Admin page). */
import { ReactNode, useEffect, useState } from "react";
import { Badge, Button, Card, Empty, ErrorBox, Field } from "@/components/ui";
import { api, useApi } from "@/lib/api";

type Cfg = Record<string, any>;
type Change = (p: string[], v: any) => void;

function Num({ cfg, path, label, hint, onChange }: { cfg: Cfg; path: string[]; label: string; hint?: string; onChange: Change }) {
  const v = path.reduce<any>((o, k) => o?.[k], cfg) as number | null;
  return <Field label={label} hint={hint}><input value={v ?? ""} onChange={(e) => onChange(path, e.target.value === "" ? null : Number(e.target.value))} /></Field>;
}

function Hard({ cfg, name, label, onChange }: { cfg: Cfg; name: string; label?: string; onChange: Change }) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" checked={!!cfg[name]?.hard} onChange={(e) => onChange([name, "hard"], e.target.checked)} />
      {label ?? "Required for Top 5"} <span className="text-xs text-muted">(unticked = ranking preference only)</span>
    </label>
  );
}

/** Collapsible card holding the criteria for one tab. */
export function CriteriaCard({ title, tab, sections, defaultOpen = false }: {
  title: string; tab: string; sections: ("options" | "insider" | "ranking" | "shariah")[]; defaultOpen?: boolean;
}) {
  const s = useApi<{ config: Cfg; relaxed: string[]; methodologies: Record<string, any>; defaults: Cfg }>("/admin/settings");
  const [open, setOpen] = useState(defaultOpen);
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- editable copy once data arrives
  useEffect(() => { if (s.data && !cfg) setCfg(structuredClone(s.data.config)); }, [s.data, cfg]);

  const change: Change = (p, v) => setCfg((c) => {
    const n = structuredClone(c!);
    let o = n;
    for (const k of p.slice(0, -1)) o = o[k];
    o[p.at(-1)!] = v;
    return n;
  });
  async function save() {
    setErr(null); setMsg(null);
    try {
      const r = await api<{ relaxed: string[] }>("/admin/settings", { method: "PUT", body: JSON.stringify({ config: cfg, note: `Edited on the ${tab} tab` }) });
      setMsg(r.relaxed.length ? `Saved. Looser than the original specification: ${r.relaxed.join(", ")}.` : "Saved. Applies from the next scan.");
      s.reload();
    } catch (e) { setErr((e as Error).message); }
  }
  function reset() {
    const d = s.data!.defaults;
    setCfg((c) => {
      const n = structuredClone(c!);
      const keys = sections.flatMap((k) => k === "ranking" ? ["technical", "analyst", "float", "liquidity", "weights"] : [k]);
      for (const k of keys) n[k] = structuredClone(d[k]);
      return n;
    });
  }

  let body: ReactNode = <Empty>Loading…</Empty>;
  if (cfg && s.data) {
    const m = s.data.methodologies;
    body = (
      <div className="space-y-5">
        {sections.includes("options") && (
          <section className="space-y-2">
            <Hard cfg={cfg} name="options" onChange={change} />
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
              <Field label="Options data source"><select value={cfg.options.source} onChange={(e) => change(["options", "source"], e.target.value)}>
                <option value="csv">CSV import (uploaded file)</option><option value="orats">ORATS API</option></select></Field>
              <Num cfg={cfg} path={["options", "min_volume_change_pct"]} label="1M volume change ≥ (%)" hint="spec: 300" onChange={change} />
              <Num cfg={cfg} path={["options", "min_iv_rank"]} label="IV rank greater than" hint="spec: 20" onChange={change} />
              <Num cfg={cfg} path={["options", "max_put_call_ratio"]} label="Put/call below" hint="spec: 0.20" onChange={change} />
              <Num cfg={cfg} path={["options", "min_baseline_volume"]} label="Min baseline volume" hint="ORATS only" onChange={change} />
              <Field label="Baseline method" hint="ORATS only"><select value={cfg.options.baseline_method} onChange={(e) => change(["options", "baseline_method"], e.target.value)}>
                <option value="trailing_20d_avg">20-session average, one month earlier</option><option value="single_session_1m_ago">Single session 21 sessions earlier</option></select></Field>
            </div>
          </section>
        )}
        {sections.includes("insider") && (
          <section className="space-y-2">
            <Hard cfg={cfg} name="insider" onChange={change} />
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
              <Num cfg={cfg} path={["insider", "min_pct_outstanding"]} label="Bought > % of shares outstanding" hint="spec: 1.0" onChange={change} />
              <Field label="Look-back"><select value={cfg.insider.lookback_days} onChange={(e) => change(["insider", "lookback_days"], Number(e.target.value))}>{[7, 30, 60, 90].map((d) => <option key={d} value={d}>{d} days</option>)}</select></Field>
              <Field label="Basis"><select value={cfg.insider.basis} onChange={(e) => change(["insider", "basis"], e.target.value)}><option value="aggregate_unique">Aggregate of unique purchases</option><option value="single_transaction">Largest single purchase</option></select></Field>
            </div>
          </section>
        )}
        {sections.includes("ranking") && (
          <>
            <section className="space-y-2"><h3 className="text-sm font-semibold">Technical support</h3><Hard cfg={cfg} name="technical" onChange={change} />
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <Num cfg={cfg} path={["technical", "support_proximity_pct"]} label="Max distance to support (%)" hint="default 3" onChange={change} />
                <Num cfg={cfg} path={["technical", "strong_pct"]} label="Strong support ≤ (%)" onChange={change} />
                <Num cfg={cfg} path={["technical", "approaching_pct"]} label="Approaching ≤ (%)" onChange={change} />
                <Num cfg={cfg} path={["technical", "min_zone_touches"]} label="Min touches for a zone" onChange={change} />
              </div></section>
            <section className="space-y-2"><h3 className="text-sm font-semibold">Analysts / float / liquidity</h3>
              <div className="flex flex-wrap gap-4"><Hard cfg={cfg} name="analyst" label="Analysts required" onChange={change} /><Hard cfg={cfg} name="float" label="Float required" onChange={change} /><Hard cfg={cfg} name="liquidity" label="Liquidity required" onChange={change} /></div>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <Num cfg={cfg} path={["analyst", "min_coverage"]} label="Min analyst coverage" onChange={change} />
                <Num cfg={cfg} path={["float", "very_low_pct"]} label="Very low float < (%)" onChange={change} />
                <Num cfg={cfg} path={["float", "low_pct"]} label="Low float < (%)" onChange={change} />
                <Num cfg={cfg} path={["liquidity", "min_avg_dollar_volume"]} label="Min avg $ volume" onChange={change} />
              </div></section>
            <section className="space-y-2"><h3 className="text-sm font-semibold">Ranking weights (must total 100)</h3>
              <div className="grid grid-cols-3 gap-3 md:grid-cols-6">{["options", "insider", "analyst", "technical", "float", "liquidity"].map((k) =>
                <Num key={k} cfg={cfg} path={["weights", k]} label={k} onChange={change} />)}</div></section>
          </>
        )}
        {sections.includes("shariah") && (
          <section className="space-y-2"><h3 className="text-sm font-semibold">Shariah methodology (green / red highlighting only)</h3>
            <select value={cfg.shariah.methodology} onChange={(e) => change(["shariah", "methodology"], e.target.value)}>
              {Object.entries(m).map(([k, x]: [string, any]) => <option key={k} value={k}>{x.label}</option>)}</select>
            <p className="text-xs text-muted">Denominator: {m[cfg.shariah.methodology].denominator} · debt &lt; {m[cfg.shariah.methodology].debt}% · cash &lt; {m[cfg.shariah.methodology].cash}%</p>
          </section>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={save}>Save criteria</Button>
          <Button variant="ghost" onClick={reset}>Reset to specification</Button>
          {msg && <span className="text-sm text-bull">{msg}</span>}
        </div>
        <ErrorBox error={err} />
      </div>
    );
  }
  return (
    <Card title={<button className="flex items-center gap-2" onClick={() => setOpen(!open)}><span className="text-muted">{open ? "▾" : "▸"}</span>{title}</button>}
      right={!open && s.data?.relaxed.length ? <Badge tone="warn">relaxed: {s.data.relaxed.join(", ")}</Badge> : undefined}>
      {open ? body : <p className="text-xs text-muted">Click to view or change.</p>}
    </Card>
  );
}

/** Shariah business-activity reviews and notification preferences (Watchlist tab). */
export function ShariahReviews() {
  const reviews = useApi<any[]>("/admin/business-reviews");
  const [open, setOpen] = useState(false);
  const [rv, setRv] = useState({ ticker: "", status: "compliant", notes: "", prohibited_revenue_pct: "" });
  const [err, setErr] = useState<string | null>(null);
  async function add() {
    setErr(null);
    try {
      await api("/admin/business-reviews", { method: "POST", body: JSON.stringify({ ...rv, prohibited_revenue_pct: rv.prohibited_revenue_pct === "" ? null : Number(rv.prohibited_revenue_pct) }) });
      setRv({ ticker: "", status: "compliant", notes: "", prohibited_revenue_pct: "" }); reviews.reload();
    } catch (e) { setErr((e as Error).message); }
  }
  return (
    <Card title={<button className="flex items-center gap-2" onClick={() => setOpen(!open)}><span className="text-muted">{open ? "▾" : "▸"}</span>Shariah business-activity reviews</button>}>
      {!open ? <p className="text-xs text-muted">Record your own review of a company&apos;s business to override its green / red highlight.</p> : (
        <>
          <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-5">
            <Field label="Ticker"><input value={rv.ticker} onChange={(e) => setRv({ ...rv, ticker: e.target.value })} /></Field>
            <Field label="Decision"><select value={rv.status} onChange={(e) => setRv({ ...rv, status: e.target.value })}><option value="compliant">Business compliant</option><option value="non_compliant">Business non-compliant</option></select></Field>
            <Field label="Prohibited revenue (%)" hint="mixed-revenue firms"><input value={rv.prohibited_revenue_pct} onChange={(e) => setRv({ ...rv, prohibited_revenue_pct: e.target.value })} /></Field>
            <Field label="Notes / evidence"><input value={rv.notes} onChange={(e) => setRv({ ...rv, notes: e.target.value })} /></Field>
            <div className="flex items-end"><Button onClick={add} disabled={!rv.ticker}>Record review</Button></div>
          </div>
          <ErrorBox error={err} />
          {reviews.data?.length ? <table className="text-sm"><thead><tr><th>Ticker</th><th>Decision</th><th>Prohibited rev.</th><th>Date</th><th>Notes</th></tr></thead>
            <tbody>{reviews.data.map((r, i) => <tr key={i} className={r.superseded ? "opacity-50" : ""}><td>{r.ticker}</td><td>{r.status}{r.superseded && " (superseded)"}</td><td>{r.prohibited_revenue_pct ?? "—"}</td><td>{r.reviewed_on}</td><td className="max-w-md truncate">{r.notes}</td></tr>)}</tbody></table>
            : <Empty>No manual reviews. Stocks in review industries (REITs, hotels, brokers…) stay unscreened (blue) until reviewed.</Empty>}
        </>
      )}
    </Card>
  );
}

export function NotificationPrefs() {
  const me = useApi<{ notify_email: boolean; notify_telegram: boolean; telegram_chat_id: string | null }>("/me");
  const [np, setNp] = useState({ notify_email: false, notify_telegram: false, telegram_chat_id: "" });
  const [msg, setMsg] = useState<string | null>(null);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- editable copy once data arrives
  useEffect(() => { if (me.data) setNp({ notify_email: me.data.notify_email, notify_telegram: me.data.notify_telegram, telegram_chat_id: me.data.telegram_chat_id ?? "" }); }, [me.data]);
  async function save() {
    await api("/me/notifications", { method: "PUT", body: JSON.stringify({ ...np, telegram_chat_id: np.telegram_chat_id || null }) });
    setMsg("Saved.");
  }
  return (
    <div className="flex flex-wrap items-end gap-4">
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={np.notify_email} onChange={(e) => setNp({ ...np, notify_email: e.target.checked })} /> Email</label>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={np.notify_telegram} onChange={(e) => setNp({ ...np, notify_telegram: e.target.checked })} /> Telegram</label>
      <Field label="Telegram chat ID"><input value={np.telegram_chat_id} onChange={(e) => setNp({ ...np, telegram_chat_id: e.target.value })} /></Field>
      <Button variant="ghost" onClick={save}>Save alerts</Button>
      {msg && <span className="text-sm text-bull">{msg}</span>}
      <p className="w-full text-xs text-muted">Alert when a new stock enters the Top 5 (needs SMTP / Telegram bot settings on the server).</p>
    </div>
  );
}
