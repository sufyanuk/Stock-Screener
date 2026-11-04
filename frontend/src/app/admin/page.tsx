"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useState } from "react";
import { OptionsImport } from "@/components/options-import";
import { Badge, Button, Card, Empty, ErrorBox, Field } from "@/components/ui";
import { api, useApi } from "@/lib/api";

type Cfg = Record<string, any>;

function NumberField({ cfg, path, label, hint, onChange }: { cfg: Cfg; path: string[]; label: string; hint?: string; onChange: (p: string[], v: any) => void }) {
  const v = path.reduce<any>((o, k) => o?.[k], cfg) as number | null;
  return <Field label={label} hint={hint}><input value={v ?? ""} onChange={(e) => onChange(path, e.target.value === "" ? null : Number(e.target.value))} /></Field>;
}

function HardToggle({ cfg, name, onChange }: { cfg: Cfg; name: string; onChange: (p: string[], v: any) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" checked={!!cfg[name]?.hard} onChange={(e) => onChange([name, "hard"], e.target.checked)} />
      Hard filter <span className="text-xs text-muted">(unchecked = ranking preference only)</span>
    </label>
  );
}

export default function AdminPage() {
  const s = useApi<{ config: Cfg; relaxed: string[]; methodologies: Record<string, any>; defaults: Cfg }>("/admin/settings");
  const me = useApi<{ role: string; notify_email: boolean; notify_telegram: boolean; telegram_chat_id: string | null }>("/me");
  const reviews = useApi<any[]>("/admin/business-reviews");
  const health = useApi<any>("/health");
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [rv, setRv] = useState({ ticker: "", status: "compliant", notes: "", prohibited_revenue_pct: "" });
  const [np, setNp] = useState<{ notify_email: boolean; notify_telegram: boolean; telegram_chat_id: string }>({ notify_email: false, notify_telegram: false, telegram_chat_id: "" });

  // eslint-disable-next-line react-hooks/set-state-in-effect -- initialise editable copies once data arrives
  useEffect(() => { if (s.data && !cfg) setCfg(structuredClone(s.data.config)); }, [s.data, cfg]);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- initialise editable copies once data arrives
  useEffect(() => { if (me.data) setNp({ notify_email: me.data.notify_email, notify_telegram: me.data.notify_telegram, telegram_chat_id: me.data.telegram_chat_id ?? "" }); }, [me.data]);

  const isAdmin = me.data?.role === "admin";
  const change = (p: string[], v: any) => setCfg((c) => {
    const n = structuredClone(c!);
    let o = n;
    for (const k of p.slice(0, -1)) o = o[k];
    o[p.at(-1)!] = v;
    return n;
  });
  async function save() {
    setErr(null); setMsg(null);
    try {
      const r = await api<{ relaxed: string[] }>("/admin/settings", { method: "PUT", body: JSON.stringify({ config: cfg, note }) });
      setMsg(r.relaxed.length ? `Saved. Relaxed vs. specification: ${r.relaxed.join(", ")} — this is shown on every affected scan.` : "Saved. All thresholds meet the specification.");
      s.reload();
    } catch (e) { setErr((e as Error).message); }
  }
  async function addReview() {
    setErr(null);
    try {
      await api("/admin/business-reviews", { method: "POST", body: JSON.stringify({ ...rv, prohibited_revenue_pct: rv.prohibited_revenue_pct === "" ? null : Number(rv.prohibited_revenue_pct) }) });
      setRv({ ticker: "", status: "compliant", notes: "", prohibited_revenue_pct: "" }); reviews.reload();
    } catch (e) { setErr((e as Error).message); }
  }
  async function saveNotify() {
    await api("/me/notifications", { method: "PUT", body: JSON.stringify({ ...np, telegram_chat_id: np.telegram_chat_id || null }) });
    setMsg("Notification preferences saved.");
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Settings</h1>
      <ErrorBox error={err || s.error} />
      {msg && <div className="rounded-lg border border-accent/40 bg-accent/10 p-3 text-sm">{msg}</div>}

      <OptionsImport isAdmin={isAdmin} />

      <Card title="Notifications (your account)">
        <div className="flex flex-wrap items-end gap-4">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={np.notify_email} onChange={(e) => setNp({ ...np, notify_email: e.target.checked })} /> Email</label>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={np.notify_telegram} onChange={(e) => setNp({ ...np, notify_telegram: e.target.checked })} /> Telegram</label>
          <Field label="Telegram chat ID"><input value={np.telegram_chat_id} onChange={(e) => setNp({ ...np, telegram_chat_id: e.target.value })} /></Field>
          <Button onClick={saveNotify}>Save</Button>
        </div>
        <p className="mt-2 text-xs text-muted">You are notified once per session when a new stock enters the Top 5. SMTP / bot token must be configured on the server.</p>
      </Card>

      <Card title="Provider configuration">
        {health.data ? (
          <div className="flex flex-wrap gap-2">{Object.entries(health.data.providers_configured).map(([k, v]: [string, any]) => <Badge key={k} tone={v === "configured" ? "bull" : String(v).startsWith("not needed") ? "muted" : "warn"}>{k}: {v}</Badge>)}</div>
        ) : <Empty>Loading…</Empty>}
      </Card>

      {cfg && (
        <Card title="Screening thresholds" right={!isAdmin && <Badge tone="warn">read-only (admin required)</Badge>}>
          <fieldset disabled={!isAdmin} className="space-y-5">
            <div className="rounded-lg border border-line p-3 text-sm text-muted">
              <b className="text-ink">Mandatory, not configurable:</b> Shariah status must be Compliant · price must be below $100 · missing data never passes · at most 5 recommendations, never padded.
            </div>
            <section className="space-y-2"><h3 className="text-sm font-semibold">Unusual options</h3><HardToggle cfg={cfg} name="options" onChange={change} />
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <Field label="Options data source">
                  <select value={cfg.options.source} onChange={(e) => change(["options", "source"], e.target.value)}>
                    <option value="csv">CSV import (uploaded file)</option><option value="orats">ORATS API</option>
                  </select>
                </Field>
                <NumberField cfg={cfg} path={["options", "min_volume_change_pct"]} label="Min volume change (%)" hint="spec: 300" onChange={change} />
                <NumberField cfg={cfg} path={["options", "min_iv_rank"]} label="IV rank greater than" hint="spec: 20" onChange={change} />
                <NumberField cfg={cfg} path={["options", "max_put_call_ratio"]} label="Put/call below" hint="spec: 0.20" onChange={change} />
                <NumberField cfg={cfg} path={["options", "min_baseline_volume"]} label="Min baseline volume" onChange={change} />
                <Field label="Baseline method"><select value={cfg.options.baseline_method} onChange={(e) => change(["options", "baseline_method"], e.target.value)}>
                  <option value="trailing_20d_avg">20-session average, one month earlier</option><option value="single_session_1m_ago">Single session 21 sessions earlier</option></select></Field>
              </div></section>
            <section className="space-y-2"><h3 className="text-sm font-semibold">Insider buying</h3><HardToggle cfg={cfg} name="insider" onChange={change} />
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <NumberField cfg={cfg} path={["insider", "min_pct_outstanding"]} label="% of shares outstanding greater than" hint="spec: 1.0" onChange={change} />
                <Field label="Look-back"><select value={cfg.insider.lookback_days} onChange={(e) => change(["insider", "lookback_days"], Number(e.target.value))}>{[7, 30, 60, 90].map((d) => <option key={d} value={d}>{d} days</option>)}</select></Field>
                <Field label="Basis"><select value={cfg.insider.basis} onChange={(e) => change(["insider", "basis"], e.target.value)}><option value="aggregate_unique">Aggregate of unique purchases</option><option value="single_transaction">Largest single purchase</option></select></Field>
              </div></section>
            <section className="space-y-2"><h3 className="text-sm font-semibold">Technical support</h3><HardToggle cfg={cfg} name="technical" onChange={change} />
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <NumberField cfg={cfg} path={["technical", "support_proximity_pct"]} label="Max distance to support (%)" hint="default 3" onChange={change} />
                <NumberField cfg={cfg} path={["technical", "strong_pct"]} label="Strong support ≤ (%)" onChange={change} />
                <NumberField cfg={cfg} path={["technical", "approaching_pct"]} label="Approaching ≤ (%)" onChange={change} />
                <NumberField cfg={cfg} path={["technical", "min_zone_touches"]} label="Min touches for a zone" onChange={change} />
              </div></section>
            <section className="space-y-2"><h3 className="text-sm font-semibold">Analyst / float / liquidity</h3>
              <div className="flex flex-wrap gap-4"><HardToggle cfg={cfg} name="analyst" onChange={change} /><HardToggle cfg={cfg} name="float" onChange={change} /><HardToggle cfg={cfg} name="liquidity" onChange={change} /></div>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <NumberField cfg={cfg} path={["analyst", "min_coverage"]} label="Min analyst coverage" onChange={change} />
                <NumberField cfg={cfg} path={["float", "very_low_pct"]} label="Very low float <" onChange={change} />
                <NumberField cfg={cfg} path={["float", "low_pct"]} label="Low float <" onChange={change} />
                <NumberField cfg={cfg} path={["liquidity", "min_avg_dollar_volume"]} label="Min avg $ volume" onChange={change} />
              </div></section>
            <section className="space-y-2"><h3 className="text-sm font-semibold">Ranking weights (must total 100)</h3>
              <div className="grid grid-cols-3 gap-3 md:grid-cols-6">{["options", "insider", "analyst", "technical", "float", "liquidity"].map((k) =>
                <NumberField key={k} cfg={cfg} path={["weights", k]} label={k} onChange={change} />)}</div></section>
            <section className="space-y-2"><h3 className="text-sm font-semibold">Shariah methodology</h3>
              <select value={cfg.shariah.methodology} onChange={(e) => change(["shariah", "methodology"], e.target.value)}>
                {Object.entries(s.data!.methodologies).map(([k, m]: [string, any]) => <option key={k} value={k}>{m.label}</option>)}</select>
              <p className="text-xs text-muted">Denominator: {s.data!.methodologies[cfg.shariah.methodology].denominator} · debt &lt; {s.data!.methodologies[cfg.shariah.methodology].debt}% · cash &lt; {s.data!.methodologies[cfg.shariah.methodology].cash}%</p>
            </section>
            <div className="flex flex-wrap items-end gap-3">
              <Field label="Change note (required, kept in audit log)"><input className="w-80" value={note} onChange={(e) => setNote(e.target.value)} /></Field>
              <Button onClick={save} disabled={note.length < 3}>Save thresholds</Button>
              <Button variant="ghost" onClick={() => setCfg(structuredClone(s.data!.defaults))}>Reset to specification</Button>
            </div>
          </fieldset>
        </Card>
      )}

      <Card title="Shariah business-activity reviews">
        {isAdmin && (
          <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-5">
            <Field label="Ticker"><input value={rv.ticker} onChange={(e) => setRv({ ...rv, ticker: e.target.value })} /></Field>
            <Field label="Decision"><select value={rv.status} onChange={(e) => setRv({ ...rv, status: e.target.value })}><option value="compliant">Business compliant</option><option value="non_compliant">Business non-compliant</option></select></Field>
            <Field label="Prohibited revenue (%)" hint="mixed-revenue firms"><input value={rv.prohibited_revenue_pct} onChange={(e) => setRv({ ...rv, prohibited_revenue_pct: e.target.value })} /></Field>
            <Field label="Notes / evidence"><input value={rv.notes} onChange={(e) => setRv({ ...rv, notes: e.target.value })} /></Field>
            <div className="flex items-end"><Button onClick={addReview}>Record review</Button></div>
          </div>
        )}
        {reviews.data?.length ? <table className="text-sm"><thead><tr><th>Ticker</th><th>Decision</th><th>Prohibited rev.</th><th>Reviewer</th><th>Date</th><th>Notes</th></tr></thead>
          <tbody>{reviews.data.map((r, i) => <tr key={i} className={r.superseded ? "opacity-50" : ""}><td>{r.ticker}</td><td>{r.status}{r.superseded && " (superseded)"}</td><td>{r.prohibited_revenue_pct ?? "—"}</td><td>{r.reviewer}</td><td>{r.reviewed_on}</td><td className="max-w-md truncate">{r.notes}</td></tr>)}</tbody></table>
          : <Empty>No manual reviews. Stocks in review industries (REITs, hotels, brokers…) stay “Needs Review” until reviewed.</Empty>}
      </Card>
    </div>
  );
}
