"use client";
import Link from "next/link";
import { useState } from "react";
import { Badge, Button, Card, Empty, ErrorBox, Info, ShariahLegend, Ticker, useSort } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { ago, compact, nf, pct, tone, usd } from "@/lib/format";
import type { Dashboard, Health, Rec } from "@/lib/types";

function rr(r: Rec) {
  if (!r.entry || !r.stop || !r.target1 || r.entry <= r.stop) return null;
  return (r.target1 - r.entry) / (r.entry - r.stop);
}

function Top5({ rows }: { rows: Rec[] }) {
  const flat = rows.map((r) => {
    const o = r.criteria.options?.metrics ?? {};
    const i = r.criteria.insider?.metrics ?? {};
    const a = r.criteria.analyst?.metrics ?? {};
    const t = r.criteria.technical?.metrics ?? {};
    const f = r.criteria.float?.metrics ?? {};
    return {
      ...r, vol_chg: o.volume_change_pct, ivr: o.iv_rank, pcr: o.put_call_ratio, ins_shares: i.aggregate_shares,
      ins_pct: i.aggregate_pct, consensus: a.consensus, upside: a.upside_pct, sup: t.support_distance_pct,
      float_ratio: f.float_ratio_pct, rr: rr(r), shariah: r.extra?.shariah?.status,
    };
  });
  const { sorted, th } = useSort(flat, "rank" as keyof (typeof flat)[number], false);
  if (!rows.length) return <Empty>No stocks met every requirement in the latest scan. Nothing has been substituted — see the watchlist for near-qualified names.</Empty>;
  return (
    <div className="overflow-x-auto">
      <table className="text-sm">
        <thead>
          <tr>
            {th("rank", "#")}{th("ticker", "Ticker")}<th>Company</th>{th("price_at_rec", "Price")}{th("day_change_pct", "Day")}
            {th("vol_chg", <>Opt vol Δ<Info>Current-session options volume vs. the mean of the 20 sessions ending 21 sessions earlier. (current ÷ baseline − 1) × 100.</Info></>)}
            {th("ivr", <>IV rank<Info>(IV30 now − 52-week low) ÷ (52-week high − low) × 100, using 252 sessions of 30-day ATM implied volatility.</Info></>)}
            {th("pcr", <>P/C<Info>Put volume ÷ call volume for the evaluation session (volume, not open interest).</Info></>)}
            {th("ins_shares", "Insider sh.")}
            {th("ins_pct", <>Insider %<Info>Unique open-market purchases (Form 4 code P) in the look-back window ÷ shares outstanding. Joint filings of one purchase are counted once.</Info></>)}
            {th("consensus", "Analysts")}{th("upside", "Upside")}
            {th("sup", <>To support<Info>(Price − top of nearest validated support zone) ÷ zone top × 100. A zone needs ≥ 2 swing-low touches.</Info></>)}
            {th("float_ratio", <>Float<Info>Public float ÷ shares outstanding × 100.</Info></>)}
            {th("score", <>Score<Info>Weighted sum of six 0–100 factor scores (options 25, insider 20, analyst 15, technical 20, float 10, liquidity 10).</Info></>)}
            <th>Entry</th><th>Stop</th><th>T1</th><th>T2</th>{th("rr", "R/R")}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.id}>
              <td className="num">{r.rank}</td>
              <td><Ticker symbol={r.ticker} status={r.shariah} /></td>
              <td className="max-w-48 truncate text-muted">{r.company}</td>
              <td className="num">{usd(r.price_at_rec)}</td>
              <td className={`num ${tone(r.day_change_pct)}`}>{pct(r.day_change_pct, 2, true)}</td>
              <td className="num text-bull">{pct(r.vol_chg, 0, true)}</td>
              <td className="num">{nf(r.ivr, 1)}</td>
              <td className="num">{nf(r.pcr, 3)}</td>
              <td className="num">{compact(r.ins_shares)}</td>
              <td className="num">{pct(r.ins_pct, 2)}</td>
              <td>{r.consensus ?? "—"}</td>
              <td className={`num ${tone(r.upside)}`}>{pct(r.upside, 1, true)}</td>
              <td className="num">{pct(r.sup, 2)}</td>
              <td className="num">{pct(r.float_ratio, 1)}</td>
              <td className="num font-semibold">{nf(r.score, 1)}</td>
              <td className="num">{usd(r.entry)}</td>
              <td className="num text-bear">{usd(r.stop)}</td>
              <td className="num text-bull">{usd(r.target1)}</td>
              <td className="num text-bull">{usd(r.target2)}</td>
              <td className="num">{r.rr ? `${nf(r.rr, 2)}R` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HealthPanel({ rows }: { rows: Health[] }) {
  if (!rows.length) return <Empty>No provider calls recorded yet. Run a scan after configuring API keys.</Empty>;
  return (
    <div className="space-y-2">
      {rows.map((h) => (
        <div key={h.provider} className="flex items-center justify-between rounded-lg border border-line bg-panel-2 px-3 py-2 text-sm">
          <div>
            <div className="font-medium">{h.provider}</div>
            <div className="text-xs text-muted">last OK {ago(h.last_success_at)} · {h.failures}/{h.calls} failed</div>
            {h.last_error && <div className="max-w-xs truncate text-xs text-bear" title={h.last_error}>{h.last_error}</div>}
          </div>
          <Badge tone={h.state === "fresh" ? "bull" : h.state === "stale" ? "warn" : "bear"}>{h.state}</Badge>
        </div>
      ))}
    </div>
  );
}

export default function DashboardPage() {
  const { data, error, reload } = useApi<Dashboard>("/dashboard", 60_000);
  const [scanMsg, setScanMsg] = useState<string | null>(null);
  async function scan() {
    setScanMsg("Scanning… this can take a few minutes.");
    try {
      const r = await api<{ queued: boolean; status?: string; new?: string[] }>("/scan", { method: "POST" });
      if (r.queued) {
        setScanMsg("Scan queued — results appear when it finishes.");
        setTimeout(reload, 15_000);
      } else {
        setScanMsg(`Scan finished (${r.status}).${r.new?.length ? ` New: ${r.new.join(", ")}` : ""}`);
        reload();
      }
    } catch (e) {
      setScanMsg((e as Error).message);
    }
  }
  const run = data?.latest_run;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Daily Top 5</h1>
          <p className="text-sm text-muted">
            Market: <span className="text-ink">{data?.market_phase ?? "…"}</span>
            {run && <> · Session {run.session_date} · {run.kind} scan · finished {ago(run.finished_at)} · status <Badge tone={run.status === "ok" ? "bull" : "warn"}>{run.status}</Badge></>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {scanMsg && <span className="text-xs text-muted">{scanMsg}</span>}
          <Button onClick={scan}>Run scan now</Button>
        </div>
      </div>
      <ErrorBox error={error} />
      {data?.options_source?.type === "csv" && (() => {
        const o = data.options_source!;
        if (!o.latest)
          return <div className="rounded-lg border border-warn/40 bg-warn/10 p-3 text-sm text-warn">No unusual-options CSV imported yet — upload one under <Link className="underline" href="/admin">Admin → Unusual options CSV import</Link>. Until then nothing can qualify.</div>;
        return (
          <div className={`rounded-lg border p-3 text-sm ${o.current ? "border-line text-muted" : "border-warn/40 bg-warn/10 text-warn"}`}>
            Options data: CSV import #{o.latest.id} <span className="text-ink">{o.latest.filename}</span> · session {o.latest.session_date} · {o.latest.rows} rows
            {!o.current && <> — older than the last completed session ({o.last_completed_session}); scans treat it as missing. <Link className="underline" href="/admin">Import a current file</Link>.</>}
          </div>
        );
      })()}
      {run && run.relaxed_criteria.length > 0 && (
        <div className="rounded-lg border border-warn/40 bg-warn/10 p-3 text-sm text-warn">
          An administrator relaxed these requirements for this scan: <b>{run.relaxed_criteria.join(", ")}</b>. Results are not the strict Top 5.
        </div>
      )}
      {run && run.errors.length > 0 && (
        <details className="rounded-lg border border-warn/40 bg-warn/10 p-3 text-sm text-warn">
          <summary>{run.errors.length} data issue(s) during this scan — affected criteria were marked missing, never passed.</summary>
          <ul className="mt-2 list-disc pl-5 text-xs">{run.errors.slice(0, 20).map((e, i) => <li key={i}>{e}</li>)}</ul>
        </details>
      )}
      {!run && data && <Empty>No completed scan yet. Configure provider keys (see SETUP.md) and click “Run scan now”.</Empty>}

      <div className="flex justify-end"><ShariahLegend /></div>
      <Card title="Recommendations" right={run && <span className="text-xs text-muted">Funnel: {run.funnel.stage1_evaluated ?? 0} under $100 → {run.funnel.stage1_options_pass ?? 0} options pass → {run.funnel.eligible ?? 0} fully eligible</span>}>
        <Top5 rows={data?.top ?? []} />
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Unusual options activity" className="lg:col-span-2">
          {data?.unusual_options.length ? (
            <div className="overflow-x-auto"><table className="text-sm"><thead><tr><th>Ticker</th><th>Volume</th><th>Baseline</th><th>Δ</th><th>Calls</th><th>Puts</th><th>P/C</th><th>IV rank</th><th>Session</th></tr></thead>
              <tbody>{data.unusual_options.map((o) => (
                <tr key={o.ticker}><td><Ticker symbol={o.ticker} status={o.shariah_status} /></td><td className="num">{compact(o.current_volume)}</td>
                  <td className="num">{compact(o.baseline_volume)}</td><td className="num text-bull">{pct(o.volume_change_pct, 0, true)}</td>
                  <td className="num">{compact(o.call_volume)}</td><td className="num">{compact(o.put_volume)}</td><td className="num">{nf(o.put_call_ratio, 3)}</td>
                  <td className="num">{nf(o.iv_rank, 1)}</td><td className="text-muted">{o.current_session}</td></tr>))}</tbody></table></div>
          ) : <Empty>No stocks passed the options filter in the latest scan.</Empty>}
        </Card>
        <Card title="Data freshness & API health"><HealthPanel rows={data?.provider_health ?? []} /></Card>
      </div>

      <div className="grid gap-4">
        <Card title="Latest insider buying (open-market, Form 4)">
          {data?.insider_buys.length ? (
            <div className="overflow-x-auto"><table className="text-sm"><thead><tr><th>Ticker</th><th>Insider</th><th>Role</th><th>Trade</th><th>Filed</th><th>Shares</th><th>Price</th><th>Value</th></tr></thead>
              <tbody>{data.insider_buys.map((b, i) => (
                <tr key={i}><td><Ticker symbol={b.ticker} status={b.shariah_status} /></td><td className="max-w-44 truncate" title={b.co_reporters?.length ? `Also reported by: ${b.co_reporters.join(", ")}` : undefined}>
                    {b.owner}{b.co_reporters?.length ? <span className="text-muted"> (+{b.co_reporters.length} joint)</span> : null}</td><td className="max-w-36 truncate text-muted">{b.title}</td>
                  <td>{b.transaction_date}</td><td>{b.filing_date}</td><td className="num">{compact(b.shares)}</td><td className="num">{usd(b.price)}</td><td className="num">{compact(b.value)}</td></tr>))}</tbody></table></div>
          ) : <Empty>No open-market insider purchases filed in the last 7 days have been ingested.</Empty>}
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Low-float opportunities">
          {data?.low_float.length ? data.low_float.map((s) => (
            <div key={s.ticker} className="flex justify-between border-b border-line py-1.5 text-sm"><Ticker symbol={s.ticker} status={s.shariah_status} />
              <span className="text-muted">{s.category}</span><span className="num">{pct(s.float_ratio_pct, 1)}</span></div>)) : <Empty>None in the latest scan.</Empty>}
        </Card>
        <Card title="Watchlist — near-qualified">
          {data?.watchlist.length ? data.watchlist.map((w) => (
            <div key={w.ticker} className="border-b border-line py-1.5 text-sm">
              <div className="flex justify-between"><Ticker symbol={w.ticker} status={w.shariah_status} /><span className="num">{nf(w.score, 1)}</span></div>
              <div className="text-xs text-bear">fails: {w.failures.join(", ")}</div>
            </div>)) : <Empty>No near-qualified stocks.</Empty>}
        </Card>
      </div>
    </div>
  );
}
