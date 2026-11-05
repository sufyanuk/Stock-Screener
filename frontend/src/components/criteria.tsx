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
  title: string; tab: string; sections: ("options" | "insider")[]; defaultOpen?: boolean;
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
      for (const k of sections) n[k] = structuredClone(d[k]);
      return n;
    });
  }

  let body: ReactNode = <Empty>Loading…</Empty>;
  if (cfg && s.data) {
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
