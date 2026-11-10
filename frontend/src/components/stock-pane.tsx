"use client";
import Link from "next/link";
import { useMemo } from "react";
import { PriceChart, type Bar } from "@/components/charts";
import { TickerSearch } from "@/components/ticker-search";
import { TradePlanCard, type TradePlan } from "@/components/trade-plan";
import { Badge, Empty, ErrorBox, Stat, Ticker } from "@/components/ui";
import { useApi } from "@/lib/api";
import { compact, nf, pct, tone, usd } from "@/lib/format";
import { daysUntil, usePane } from "@/lib/pane-context";

type Summary = {
  ticker: string; name: string | null; price: number | null; price_source: string | null; day_change_pct: number | null;
  pe: number | null; market_cap: number | null; year_high: number | null; year_low: number | null;
  debt_to_equity: number | null; trade_plan: TradePlan;
  analysts: { counts: Record<string, number> | null; total: number | null; recommending_buy: number | null; target_avg: number | null; target_high: number | null; target_low: number | null };
  earnings: { date: string; time_of_day: string | null; eps_estimate: number | null; source: string } | null;
  bars: Bar[]; bars_provider: string | null; news: { title: string; url: string; published: string; publisher: string }[];
  options: { vol_change_1m_pct: number | null; options_volume: number | null; iv_rank: number | null; put_call: number | null; session: string } | null;
  sentisense: { rating: SsRating | null; insights: { type: string; category: string; text: string; confidence: number | null; urgency: string; generated: string | null }[] } | null;
  insider_90d: { purchases: number; shares: number; value: number; latest: string; insiders: number } | null;
  shariah_status: string | null; errors: string[];
};

const RATINGS: [string, string, string][] = [
  ["strong_buy", "Strong Buy", "bg-bull"], ["buy", "Buy", "bg-bull/60"], ["hold", "Hold", "bg-muted/60"],
  ["sell", "Sell", "bg-bear/60"], ["strong_sell", "Strong Sell", "bg-bear"],
];

type SsRating = {
  rated: boolean; as_of: string | null; reason: string | null; disclaimer: string | null; score?: number; letter?: string; percentile?: number;
  penalty_points?: number; analyst_favorable_pct?: number | null; risk_adjustments?: { condition: string; points: number }[];
  flags?: { key: string; label: string; active: boolean }[];
};

const LETTER_TONE: Record<string, string> = { A: "text-bull", B: "text-bull", C: "text-warn", D: "text-bear", F: "text-bear" };

/** SentiSense Rating (a composite score band — not a buy/sell call) and its top AI insights. */
function SentiSenseBlock({ s }: { s: NonNullable<Summary["sentisense"]> }) {
  const r = s.rating;
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-[11px] uppercase tracking-wide text-muted">
        <span>SentiSense rating (beta)</span><span className="normal-case">{r?.as_of ? `as of ${r.as_of}` : ""}</span>
      </div>
      {!r ? <p className="text-xs text-muted">Not covered by SentiSense.</p> : !r.rated ? (
        <p className="text-xs text-muted">Not rated today{r.reason ? ` (${r.reason.replaceAll("_", " ")})` : ""}.</p>
      ) : (
        <>
          <div className="flex items-baseline gap-2">
            <span className={`text-2xl font-bold ${LETTER_TONE[r.letter ?? ""] ?? ""}`}>{r.letter}</span>
            <span className="text-sm">{nf(r.score, 1)} / 100</span>
            <span className="text-xs text-muted">ranked {nf(r.percentile, 0)}th percentile{r.penalty_points ? ` · −${nf(r.penalty_points, 1)} risk points` : ""}</span>
          </div>
          {r.analyst_favorable_pct != null && <p className="text-xs text-muted">Favourable analyst ratings: {nf(r.analyst_favorable_pct, 0)}%</p>}
          {!!r.risk_adjustments?.length && <p className="text-xs text-muted">Risks: {r.risk_adjustments.map((a) => `${a.condition.replaceAll("_", " ")} (−${nf(a.points, 1)})`).join(", ")}</p>}
          {r.flags?.some((f) => f.active) && <p className="text-xs text-warn">{r.flags.filter((f) => f.active).map((f) => f.label).join(" · ")}</p>}
        </>
      )}
      {s.insights.length > 0 && (
        <ul className="mt-2 space-y-1.5 text-xs">{s.insights.map((x, i) => (
          <li key={i}><Badge tone={x.urgency === "high" ? "warn" : "muted"}>{x.urgency}</Badge> <span>{x.text}</span>
            <span className="text-muted"> · {x.type?.replaceAll("_", " ")}{x.generated ? ` · ${x.generated}` : ""}</span></li>))}</ul>
      )}
      <p className="mt-1 text-[10px] text-muted">Composite research score from SentiSense, not a recommendation. Insights are AI-generated.</p>
    </div>
  );
}

function AnalystBar({ counts }: { counts: Record<string, number> }) {
  const total = RATINGS.reduce((a, [k]) => a + (counts[k] ?? 0), 0);
  if (!total) return <p className="text-xs text-muted">No analyst ratings.</p>;
  return (
    <div>
      <div className="flex h-2 overflow-hidden rounded-full bg-panel-2">
        {RATINGS.map(([k, , c]) => (counts[k] ? <div key={k} className={c} style={{ width: `${(counts[k] / total) * 100}%` }} /> : null))}
      </div>
      <div className="mt-1.5 grid grid-cols-5 gap-1 text-center text-[11px]">
        {RATINGS.map(([k, label]) => <div key={k}><div className="num text-sm">{counts[k] ?? 0}</div><div className="text-muted">{label}</div></div>)}
      </div>
    </div>
  );
}

export function StockPane() {
  const pane = usePane();
  const t = pane?.selected ?? null;
  const { data, error, loading } = useApi<Summary>(t ? `/stocks/${encodeURIComponent(t)}/summary` : null);
  const d = data && data.ticker === t ? data : null;
  const levels = useMemo(() => [], []);
  if (!pane) return null;
  if (!t)
    return (
      <aside className="panel space-y-3 p-4">
        <div className="text-sm font-semibold">Stock details</div>
        <TickerSearch />
        <p className="text-sm text-muted">
          Click any ticker on the page, or search above, to see its price, P/E, debt-to-equity, chart, analyst
          recommendations, next earnings date and recent news here.
        </p>
      </aside>
    );
  const e = d?.earnings;
  const n = e ? daysUntil(e.date) : null;
  return (
    <aside className="panel flex max-h-[calc(100vh-5rem)] flex-col overflow-hidden">
      <header className="flex items-start justify-between gap-2 border-b border-line p-4">
        <div className="min-w-0">
          <div className="text-xl"><Ticker symbol={t} status={d?.shariah_status} link={false} /></div>
          <div className="truncate text-sm text-muted">{d?.name ?? (loading ? "Loading…" : "")}</div>
        </div>
        <button aria-label="Close panel" className="rounded-md px-2 text-lg text-muted hover:bg-panel-2 hover:text-ink" onClick={() => pane.select(null)}>×</button>
      </header>
      <div className="border-b border-line px-4 py-2"><TickerSearch /></div>
      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        <ErrorBox error={error} />
        {!d && loading && <Empty>Loading {t}…</Empty>}
        {d && (
          <>
            <div className="flex items-end gap-3">
              <span className="num text-3xl">{usd(d.price)}</span>
              <span className={`num pb-1 ${tone(d.day_change_pct)}`}>{pct(d.day_change_pct, 2, true)}</span>
            </div>
            <p className="-mt-3 text-[11px] text-muted">{d.price_source ? `Price: ${d.price_source}` : "No price source available"}</p>

            <div className="grid grid-cols-2 gap-2">
              <Stat label="P/E (TTM)" value={nf(d.pe, 1)} />
              <Stat label="Debt / equity" value={d.debt_to_equity == null ? "—" : nf(d.debt_to_equity, 2)}
                sub={d.debt_to_equity == null ? undefined : d.debt_to_equity < 0 ? "negative equity" : "total debt ÷ shareholders' equity"} />
              <Stat label="Market cap" value={d.market_cap ? `$${compact(d.market_cap)}` : "—"} />
              <Stat label="52-week range" value={<span className="text-sm">{d.year_low || d.year_high ? `${usd(d.year_low)} – ${usd(d.year_high)}` : "—"}</span>} />
            </div>

            <TradePlanCard key={d.ticker} price={d.price} plan={d.trade_plan} />

            <div className={`rounded-lg border p-3 ${n !== null && n <= 7 ? "border-warn/50 bg-warn/10" : "border-line bg-panel-2"}`}>
              <div className="text-[11px] uppercase tracking-wide text-muted">Upcoming earnings</div>
              {e ? (
                <div className="mt-1 text-sm">
                  <span className="num text-base">{e.date}</span>{e.time_of_day && <span className="text-muted"> · {e.time_of_day.replace("-", " ")}</span>}
                  {n !== null && <Badge tone={n <= 7 ? "warn" : "muted"}>{n <= 0 ? "today" : n === 1 ? "tomorrow" : `in ${n} days`}</Badge>}
                  {e.eps_estimate !== null && <div className="text-xs text-muted">EPS estimate {nf(e.eps_estimate, 2)} · {e.source}</div>}
                </div>
              ) : <div className="mt-1 text-sm text-muted">No upcoming date in the calendar.</div>}
            </div>

            <div>
              <div className="mb-1 flex items-baseline justify-between">
                <span className="text-[11px] uppercase tracking-wide text-muted">Analysts</span>
                {d.analysts.total ? <span className="text-xs"><b className="text-bull">{d.analysts.recommending_buy}</b> of {d.analysts.total} recommend Buy / Strong Buy</span> : null}
              </div>
              {d.analysts.counts ? <AnalystBar counts={d.analysts.counts} /> : <p className="text-xs text-muted">Analyst data not available.</p>}
              {d.analysts.target_avg ? <p className="mt-1 text-xs text-muted">Target {usd(d.analysts.target_avg)} ({usd(d.analysts.target_low)} – {usd(d.analysts.target_high)})
                {d.price ? <> · <span className={tone(d.analysts.target_avg - d.price)}>{pct((d.analysts.target_avg / d.price - 1) * 100, 1, true)}</span></> : null}</p> : null}
            </div>

            {d.sentisense && <SentiSenseBlock s={d.sentisense} />}

            <div>
              <div className="mb-1 flex justify-between text-[11px] uppercase tracking-wide text-muted"><span>Chart (1 year)</span><span className="normal-case">{d.bars_provider ?? ""}</span></div>
              {d.bars.length ? <PriceChart bars={d.bars} levels={levels} height={220} /> : <Empty>No price history available.</Empty>}
            </div>

            {(d.options || d.insider_90d) && (
              <div className="grid grid-cols-2 gap-2">
                {d.options && <Stat label="Unusual options" value={<span className="text-bull">{pct(d.options.vol_change_1m_pct, 0, true)}</span>}
                  sub={`vol ${compact(d.options.options_volume)} · IVR ${nf(d.options.iv_rank, 0)} · P/C ${nf(d.options.put_call, 2)}`} />}
                {d.insider_90d && <Stat label="Insider buying (90d)" value={`$${compact(d.insider_90d.value)}`}
                  sub={`${d.insider_90d.purchases} buy(s) · ${d.insider_90d.insiders} insider(s) · last ${d.insider_90d.latest}`} />}
              </div>
            )}

            <div>
              <div className="mb-1 text-[11px] uppercase tracking-wide text-muted">Recent news</div>
              {d.news.length ? (
                <ul className="space-y-2 text-sm">{d.news.map((x) => (
                  <li key={x.url}><a href={x.url} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">{x.title}</a>
                    <div className="text-[11px] text-muted">{x.publisher} · {String(x.published).slice(0, 16).replace("T", " ")}</div></li>))}</ul>
              ) : <p className="text-xs text-muted">No recent news available.</p>}
            </div>

            {d.errors.length > 0 && (
              <details className="text-[11px] text-muted"><summary>{d.errors.length} data source(s) unavailable</summary>
                <ul className="mt-1 list-disc pl-4">{d.errors.map((x) => <li key={x}>{x}</li>)}</ul></details>
            )}
          </>
        )}
      </div>
      <footer className="border-t border-line p-3 text-right">
        <Link href={`/stock/${t}`} className="text-sm text-accent hover:underline">Full analysis →</Link>
      </footer>
    </aside>
  );
}
