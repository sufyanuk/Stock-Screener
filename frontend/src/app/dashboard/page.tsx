"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import Link from "next/link";
import { ReactNode } from "react";
import { ScanBar } from "@/components/scan-bar";
import { Top5 } from "@/components/top5";
import { Card, Empty, ErrorBox, Ticker } from "@/components/ui";
import { useApi } from "@/lib/api";
import { compact, nf, pct, tone, usd } from "@/lib/format";
import type { Dashboard } from "@/lib/types";

type Col = { label: string; cell: (r: any) => ReactNode };

/** "Top 5" card summarising one tab, with a link to the full list. */
function Mini({ title, href, rows, cols, empty, loading }: { title: string; href: string; rows: any[] | undefined; cols: Col[]; empty: string; loading?: boolean }) {
  return (
    <Card title={title} right={<Link href={href} className="text-xs text-accent hover:underline">View all →</Link>}>
      {rows === undefined || (loading && !rows.length) ? <Empty>Loading…</Empty> : rows.length ? (
        <table className="text-sm">
          <thead><tr><th>Ticker</th>{cols.map((c) => <th key={c.label}>{c.label}</th>)}</tr></thead>
          <tbody>{rows.slice(0, 5).map((r, i) => (
            <tr key={(r.symbol ?? r.ticker) + i}>
              <td><Ticker symbol={r.symbol ?? r.ticker} status={r.shariah_status} /></td>
              {cols.map((c) => <td key={c.label} className="num">{c.cell(r)}</td>)}
            </tr>))}</tbody>
        </table>
      ) : <Empty>{empty}</Empty>}
    </Card>
  );
}

export default function DashboardPage() {
  const dash = useApi<Dashboard>("/dashboard", 60_000);
  const imports = useApi<{ id: number }[]>("/options-imports");
  const latest = imports.data?.[0];
  const opts = useApi<{ data: any[] }>(latest ? `/options-imports/${latest.id}` : null);
  const ins = useApi<{ rows: any[] }>("/insiders?days=90");
  const earn = useApi<{ rows: any[] }>("/earnings/upcoming?days=7");
  const low = useApi<{ rows: any[] }>("/bounces?kind=low52&limit=5");
  const ema = useApi<{ rows: any[] }>("/bounces?kind=ema50&limit=5");
  const month = new Date().getMonth() + 1;
  const seas = useApi<{ month_name: string; ranked: any[] }>(`/seasonality?month=${month}&min_years=5&min_price=5&max_price=100&limit=5`);
  const watch = useApi<{ rows: any[] }>("/custom-watchlist");
  const run = dash.data?.latest_run;

  const optRows = opts.data ? [...opts.data.data].sort((a, b) => (b.vol_change_1m_pct ?? -1e9) - (a.vol_change_1m_pct ?? -1e9)) : latest === undefined && imports.data ? [] : undefined;
  const insRows = ins.data ? [...ins.data.rows].sort((a, b) => (b.pct_outstanding ?? 0) - (a.pct_outstanding ?? 0)) : undefined;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <ScanBar title="Dashboard" onScanned={() => { dash.reload(); ins.reload(); low.reload(); ema.reload(); }} />
      </div>
      <ErrorBox error={dash.error} />

      <Card title="Top 5 — meets every rule" right={run && <span className="text-xs text-muted">Funnel: {run.funnel.stage1_evaluated ?? 0} under $100 → {run.funnel.stage1_options_pass ?? 0} options pass → {run.funnel.eligible ?? 0} fully eligible</span>}>
        {run ? <Top5 rows={dash.data?.top ?? []} /> : <Empty>No completed scan yet — import today&apos;s options CSV on the Unusual Options tab and click “Run scan now”.</Empty>}
      </Card>
      <Card title="Near-qualified — fails one or two rules">
        {dash.data?.watchlist.length ? (
          <div className="overflow-x-auto">
            <table className="text-sm">
              <thead><tr><th>Ticker</th><th>Company</th><th>Price</th><th>Score</th><th>Fails</th></tr></thead>
              <tbody>{dash.data.watchlist.map((w) => (
                <tr key={w.ticker}>
                  <td><Ticker symbol={w.ticker} status={w.shariah_status} /></td>
                  <td className="max-w-56 truncate text-muted">{w.company}</td>
                  <td className="num">{usd(w.price)}</td>
                  <td className="num">{nf(w.score, 1)}</td>
                  <td className="text-xs text-bear">{w.failures.join(", ")}</td>
                </tr>))}</tbody>
            </table>
          </div>
        ) : <Empty>No near-qualified stocks in the last scan.</Empty>}
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Mini title="Unusual options — top 5" href="/options" rows={optRows} empty="No options CSV imported yet."
          cols={[{ label: "Price", cell: (r) => usd(r.price) }, { label: "1M vol Δ", cell: (r) => <span className={tone(r.vol_change_1m_pct)}>{pct(r.vol_change_1m_pct, 0, true)}</span> },
            { label: "IV rank", cell: (r) => nf(r.iv_rank, 0) }, { label: "P/C", cell: (r) => nf(r.pc_ratio, 2) }]} />
        <Mini title="Insider buying — top 5 by % of outstanding" href="/insiders" rows={insRows} empty="No insider purchases above 1% of outstanding shares."
          cols={[{ label: "Insider", cell: (r) => <span className="block max-w-36 truncate text-left text-muted">{r.owner}</span> },
            { label: "% outstanding", cell: (r) => <span className="text-bull">{nf(r.pct_outstanding, 2)}%</span> },
            { label: "Value", cell: (r) => `$${compact(r.value)}` }, { label: "Filed", cell: (r) => <span className="text-xs">{r.filing_date}</span> }]} />
        <Mini title="Upcoming earnings — next 5" href="/earnings" rows={earn.data?.rows} empty="No earnings in the next 7 days."
          cols={[{ label: "Date", cell: (r) => <span className="text-xs">{r.date}</span> },
            { label: "Insider buying", cell: (r) => r.insider ? <span className="text-bull">${compact(r.insider.value)}</span> : "—" },
            { label: "Options 1M Δ", cell: (r) => r.options ? pct(r.options.vol_change_1m_pct, 0, true) : "—" },
            { label: "Strong Buy", cell: (r) => r.analysts?.strong_buy ?? "—" }]} />
        <Mini title={`Seasonality — best in ${seas.data?.month_name ?? ""}`} href="/seasonality" rows={seas.data?.ranked} empty="Import a price-history CSV on the Seasonality tab."
          cols={[{ label: "Price", cell: (r) => usd(r.last_close) }, { label: "Avg return", cell: (r) => <span className={tone(r.avg)}>{pct(r.avg, 2, true)}</span> },
            { label: "% years up", cell: (r) => `${nf(r.win_rate, 0)}%` }, { label: "Years", cell: (r) => r.years }]} />
        <Mini title="Bouncing from 52-week low — top 5" href="/bounces" rows={low.data?.rows} empty="No 52-week-low bounces (or price history not loaded on the Bounces tab)."
          cols={[{ label: "Price", cell: (r) => usd(r.price) }, { label: "Above low", cell: (r) => <span className="text-bull">{pct(r.above_low52_pct, 1, true)}</span> },
            { label: "Volume ×", cell: (r) => `${nf(r.vol_ratio, 1)}×` }, { label: "RSI", cell: (r) => nf(r.rsi, 0) }]} />
        <Mini title="Bouncing from 50 EMA — top 5" href="/bounces" rows={ema.data?.rows} empty="No 50-EMA bounces (or price history not loaded on the Bounces tab)."
          cols={[{ label: "Price", cell: (r) => usd(r.price) }, { label: "Close vs EMA", cell: (r) => <span className="text-bull">{pct(r.vs_ema50_pct, 2, true)}</span> },
            { label: "Volume ×", cell: (r) => `${nf(r.vol_ratio, 1)}×` }, { label: "RSI", cell: (r) => nf(r.rsi, 0) }]} />
        <Mini title="My watchlist — first 5" href="/watchlist" rows={watch.data?.rows} empty="Your watchlist is empty."
          cols={[{ label: "Close", cell: (r) => usd(r.bars?.at(-1)?.close ?? r.price) },
            { label: "Change", cell: (r) => <span className={tone(r.day_change_pct)}>{pct(r.day_change_pct, 2, true)}</span> },
            { label: "Volume", cell: (r) => compact(r.bars?.at(-1)?.volume) }]} />
      </div>
    </div>
  );
}
