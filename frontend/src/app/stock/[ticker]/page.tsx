"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import Link from "next/link";
import { use, useMemo } from "react";
import { PriceChart, type Bar, type Level } from "@/components/charts";
import { Badge, Card, Empty, ErrorBox, OutcomeBadge, ShariahLegend, Stat, Ticker } from "@/components/ui";
import { useApi } from "@/lib/api";
import { compact, nf, pct, usd } from "@/lib/format";
import type { Criterion, ScanRow } from "@/lib/types";

type StockResp = {
  ticker: string; security: { name: string; cik: string; exchange: string } | null; result: ScanRow | null; run_id: number | null;
  bars: Bar[]; bars_provider: string | null; news: { title: string; url: string; published: string; publisher: string }[];
  filings: { form: string; date: string; url: string }[]; company_profile?: Record<string, any>; disclaimer: string;
  news_error?: string; filings_error?: string;
};

const ORDER = ["options", "insider", "analyst", "technical", "float", "liquidity"];

function CriterionCard({ c }: { c: Criterion }) {
  return (
    <div className="rounded-lg border border-line bg-panel-2 p-3">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-sm font-medium capitalize">{c.name}</span>
        <span className="flex items-center gap-1.5">{c.hard && <Badge tone="info">hard filter</Badge>}<OutcomeBadge outcome={c.outcome} /><span className="num text-xs text-muted">{nf(c.score, 0)}/100</span></span>
      </div>
      <p className="text-xs leading-relaxed text-muted">{c.explanation}</p>
    </div>
  );
}

export default function StockPage({ params }: { params: Promise<{ ticker: string }> }) {
  const { ticker } = use(params);
  const { data, error } = useApi<StockResp>(`/stocks/${encodeURIComponent(ticker)}`);
  const ext = useApi<Pick<StockResp, "news" | "filings" | "company_profile" | "news_error" | "filings_error">>(`/stocks/${encodeURIComponent(ticker)}/external`);
  const r = data?.result;
  const c = r?.criteria ?? {};
  const t = c.technical?.metrics ?? {};
  const plan = t.trade_plan;
  const levels = useMemo<Level[]>(() => {
    const out: Level[] = [];
    for (const z of (t.support_zones ?? []).slice(0, 3)) out.push({ price: z.high, label: `S ${z.touches}×`, color: "#22c55e", dashed: true });
    for (const z of (t.resistance_zones ?? []).slice(0, 2)) out.push({ price: z.low, label: "R", color: "#ef4444", dashed: true });
    if (plan) {
      out.push({ price: plan.entry, label: "Entry", color: "#38bdf8" });
      out.push({ price: plan.stop_loss, label: "Stop", color: "#ef4444" });
      if (plan.target1) out.push({ price: plan.target1, label: "T1", color: "#22c55e" });
      if (plan.target2) out.push({ price: plan.target2, label: "T2", color: "#22c55e" });
    }
    return out;
  }, [t.support_zones, t.resistance_zones, plan]);

  const o = c.options?.metrics ?? {};
  const ins = c.insider?.metrics ?? {};
  const an = c.analyst?.metrics ?? {};
  const fl = c.float?.metrics ?? {};
  const prov = r?.extra?.provenance ?? {};

  return (
    <div className="space-y-4">
      <Link href="/" className="text-xs text-muted hover:text-accent">← Dashboard</Link>
      <ErrorBox error={error} />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold"><Ticker symbol={ticker.toUpperCase()} status={r?.shariah_status} link={false} /> <span className="text-base font-normal text-muted">{data?.security?.name ?? r?.company}</span></h1>
          <p className="text-sm text-muted">{data?.security?.exchange} · {ext.data?.company_profile?.sic_description ?? "—"} {ext.data?.company_profile?.website && <>· {ext.data.company_profile.website}</>}</p>
        </div>
        {r && (
          <div className="flex items-center gap-2">
            <ShariahLegend />
            <Badge tone={r.eligible ? "bull" : "warn"}>{r.eligible ? "Qualifies for Top 5" : "Does not qualify"}</Badge>
            <span className="num text-2xl">{nf(r.score, 1)}</span><span className="text-xs text-muted">/100</span>
          </div>
        )}
      </div>
      {!r && data && <Empty>This ticker has not been evaluated in a scan yet (it must pass price and options filters to reach full evaluation).</Empty>}

      {r && (
        <Card title="Why it was (or wasn't) selected">
          {r.failures.length ? <p className="mb-3 text-sm text-bear">Fails: {r.failures.join("; ")}</p> : <p className="mb-3 text-sm text-bull">Passes every mandatory requirement and every hard filter.</p>}
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">{ORDER.filter((k) => c[k]).map((k) => <CriterionCard key={k} c={c[k]} />)}</div>
        </Card>
      )}

      <div className="grid gap-4 xl:grid-cols-3">
        <Card title="Chart — support, resistance, moving averages, plan" className="xl:col-span-2"
          right={<span className="text-xs text-muted">daily bars · {data?.bars_provider ?? "—"} · last {data?.bars.at(-1)?.time ?? "—"}</span>}>
          {data?.bars.length ? <PriceChart bars={data.bars} levels={levels} /> : <Empty>No stored price history.</Empty>}
        </Card>
        <Card title="Entry, stop-loss & targets">
          {plan ? (
            <div className="grid grid-cols-2 gap-2">
              <Stat label="Entry" value={usd(plan.entry)} sub={`range ${usd(plan.entry_range[0])}–${usd(plan.entry_range[1])}`} />
              <Stat label="Stop-loss" value={<span className="text-bear">{usd(plan.stop_loss)}</span>} sub={`risk ${usd(plan.risk_per_share)}/sh`} />
              <Stat label="Target 1" value={<span className="text-bull">{usd(plan.target1)}</span>} sub={plan.rr_target1 ? `${plan.rr_target1}R` : "no resistance found"} />
              <Stat label="Target 2" value={<span className="text-bull">{usd(plan.target2)}</span>} sub={plan.rr_target2 ? `${plan.rr_target2}R` : "—"} />
              <p className="col-span-2 text-xs text-muted">{plan.method}. Use the position-size calculator before trading.</p>
              <Link href={`/tools?entry=${plan.entry}&stop=${plan.stop_loss}&t1=${plan.target1 ?? ""}&t2=${plan.target2 ?? ""}`} className="col-span-2 text-sm text-accent">Size this trade →</Link>
            </div>
          ) : <Empty>No trade plan (no validated support or insufficient data).</Empty>}
          <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
            <Stat label="RSI(14)" value={nf(t.rsi14, 1)} /><Stat label="MACD hist" value={nf(t.macd?.hist, 3)} />
            <Stat label="VWAP(20)" value={usd(t.vwap20)} /><Stat label="Rel. volume" value={`${nf(t.relative_volume, 2)}×`} />
            <Stat label="Trend D/W/M" value={<span className="text-sm">{t.trend} / {t.weekly_trend} / {t.monthly_trend}</span>} className="col-span-2" />
          </div>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Options activity">
          <div className="grid grid-cols-3 gap-2">
            <Stat label="Options volume" value={compact(o.current_volume)} sub={o.current_session} />
            <Stat label="Baseline" value={compact(o.baseline_volume)} sub={o.baseline_sessions ? `${o.baseline_sessions[0]} → ${o.baseline_sessions[1]}` : o.baseline_method} />
            <Stat label="Change" value={<span className="text-bull">{pct(o.volume_change_pct, 0, true)}</span>} />
            <Stat label="IV rank" value={nf(o.iv_rank, 1)} sub={o.iv_rank_source} />
            <Stat label="Put/Call" value={nf(o.put_call_ratio, 3)} sub={`session ${o.put_call_period ?? "—"}`} />
            <Stat label="Calls / Puts" value={`${compact(o.call_volume)} / ${compact(o.put_volume)}`} sub={o.concentration ? `concentrated in ${o.concentration}` : ""} />
          </div>
          {o.largest_contracts?.length ? (
            <div className="mt-3 overflow-x-auto"><table className="text-xs"><thead><tr><th>Contract</th><th>Exp</th><th>Strike</th><th>Vol</th><th>OI</th><th>Premium</th><th>Money</th><th>Open/close</th></tr></thead>
              <tbody>{o.largest_contracts.map((x: any) => (
                <tr key={x.contract}><td>{x.type} {x.is_block && <Badge tone="info">block</Badge>}</td><td>{x.expiration}</td><td className="num">{nf(x.strike)}</td>
                  <td className="num">{compact(x.volume)}</td><td className="num">{compact(x.open_interest)}</td><td className="num">{compact(x.premium)}</td><td>{x.moneyness}</td><td className="text-muted">{x.open_close}</td></tr>))}</tbody></table></div>
          ) : <p className="mt-3 text-xs text-muted">No per-contract data (chain snapshot unavailable for this scan).</p>}
          <p className="mt-2 text-xs text-muted">Premium uses each contract&apos;s day VWAP × volume × 100. Large call volume is not automatically bullish.</p>
        </Card>

        <Card title="Insider buying (SEC Form 4)">
          <div className="grid grid-cols-3 gap-2">
            <Stat label="Unique purchases" value={ins.unique_purchase_count ?? "—"} sub={ins.lookback_days ? `${ins.distinct_insiders ?? 0} insider(s), ${ins.lookback_days}d` : undefined} />
            <Stat label="Aggregate" value={pct(ins.aggregate_pct, 2)} sub={`${compact(ins.aggregate_shares)} sh`} />
            <Stat label="Largest single" value={pct(ins.largest_single_pct, 2)} sub={`${compact(ins.largest_single_shares)} sh`} />
          </div>
          <p className="mt-2 text-xs text-muted">Shares outstanding {compact(ins.shares_outstanding)} (SEC dei, as of {ins.shares_outstanding_date ?? "—"}). Basis: {ins.basis}.</p>
          {ins.purchases?.length ? (
            <div className="mt-2 overflow-x-auto"><table className="text-xs"><thead><tr><th>Insider</th><th>Role</th><th>Trade</th><th>Filed</th><th>Shares</th><th>Price</th><th>Value</th><th>% out.</th><th>Before→After</th></tr></thead>
              <tbody>{ins.purchases.map((p: any) => (
                <tr key={p.accession + p.owner}><td>{p.owner}{p.joint_report && <span className="text-muted"> (+{p.co_reporters.length} joint)</span>}</td><td className="max-w-32 truncate">{p.designation}</td>
                  <td>{p.transaction_date}</td><td>{p.filing_date}</td><td className="num">{compact(p.shares)}</td><td className="num">{usd(p.price)}</td>
                  <td className="num">{compact(p.value)}</td><td className="num">{pct(p.pct_outstanding, 2)}</td><td className="num">{compact(p.owned_before)}→{compact(p.owned_after)}</td></tr>))}</tbody></table></div>
          ) : null}
          {ins.excluded_count > 0 && <p className="mt-2 text-xs text-muted">{ins.excluded_count} non-qualifying transactions excluded (grants, option exercises, gifts, tax withholding, sales).</p>}
        </Card>

        <Card title="Analyst ratings">
          <div className="grid grid-cols-3 gap-2">
            <Stat label="Consensus" value={an.consensus ?? "—"} sub={`${nf(an.consensus_score, 2)}/5 · ${an.coverage ?? 0} analysts`} />
            <Stat label="Avg target" value={usd(an.target_avg)} sub={`${usd(an.target_low)} – ${usd(an.target_high)}`} />
            <Stat label="Upside" value={pct(an.upside_pct, 1, true)} />
            <Stat label="30d up/down" value={`${an.changes_30d?.upgrades ?? 0} / ${an.changes_30d?.downgrades ?? 0}`} />
            <Stat label="90d up/down" value={`${an.changes_90d?.upgrades ?? 0} / ${an.changes_90d?.downgrades ?? 0}`} />
            <Stat label="Accuracy weighting" value={an.accuracy_weighting ? "on" : "off"} sub={an.accuracy_weighting ? "verified track records" : "no verified track records"} />
          </div>
          {an.recent?.length ? <ul className="mt-2 space-y-1 text-xs">{an.recent.slice(0, 8).map((a: any, i: number) => (
            <li key={i} className="flex justify-between border-b border-line py-1"><span>{a.date} · {a.firm}</span><span className={a.action === "upgrade" ? "text-bull" : a.action === "downgrade" ? "text-bear" : "text-muted"}>{a.action}: {a.from ?? "—"} → {a.to}</span></li>))}</ul> : null}
        </Card>

        <Card title="Float & liquidity">
          <div className="grid grid-cols-3 gap-2">
            <Stat label="Shares outstanding" value={compact(fl.shares_outstanding)} />
            <Stat label="Public float" value={compact(fl.float_shares)} />
            <Stat label="Float ratio" value={pct(fl.float_ratio_pct, 1)} sub={fl.category} />
            <Stat label="Short % float" value={pct(fl.short_pct_float, 1)} />
            <Stat label="Days to cover" value={nf(fl.days_to_cover, 1)} />
            <Stat label="Avg daily volume" value={compact(fl.avg_daily_volume)} />
          </div>
          {c.liquidity?.metrics?.flags?.length ? <p className="mt-2 text-xs text-warn">Flags: {c.liquidity.metrics.flags.join("; ")}</p> : null}
        </Card>
      </div>


      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Latest news">
          {ext.loading ? <Empty>Loading news…</Empty> : ext.data?.news.length ? <ul className="space-y-2 text-sm">{ext.data.news.map((n) => (
            <li key={n.url}><a href={n.url} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">{n.title}</a><div className="text-xs text-muted">{n.publisher} · {n.published}</div></li>))}</ul>
            : <Empty>{ext.data?.news_error ? `News unavailable: ${ext.data.news_error}` : "No news."}</Empty>}
        </Card>
        <Card title="Recent SEC filings">
          {ext.loading ? <Empty>Loading filings…</Empty> : ext.data?.filings.length ? <ul className="space-y-1 text-sm">{ext.data.filings.map((f) => (
            <li key={f.url} className="flex justify-between border-b border-line py-1"><a href={f.url} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">{f.form}</a><span className="text-muted">{f.date}</span></li>))}</ul>
            : <Empty>{ext.data?.filings_error ? `Filings unavailable: ${ext.data.filings_error}` : "No filings."}</Empty>}
        </Card>
      </div>

      <Card title="Data sources & timestamps">
        <table className="text-xs"><thead><tr><th>Dataset</th><th>Provider</th><th>Freshness</th><th>Market time</th><th>Retrieved</th><th>Endpoint</th></tr></thead>
          <tbody>{Object.entries(prov).map(([k, v]: [string, any]) => (
            <tr key={k}><td>{k}</td><td>{v.provider}</td><td><Badge tone={v.freshness === "realtime" ? "bull" : v.freshness === "delayed" ? "warn" : "muted"}>{v.freshness}</Badge></td>
              <td>{v.market_ts ?? "—"}</td><td>{v.retrieved_at}</td><td className="text-muted">{v.endpoint}</td></tr>))}
            <tr><td>options</td><td colSpan={5} className="text-muted">{o.source ?? "ORATS daily cores/summaries"} · session {o.current_session ?? "—"} (end-of-day)</td></tr>
          </tbody></table>
      </Card>
      <p className="text-xs text-muted">{data?.disclaimer}</p>
    </div>
  );
}
