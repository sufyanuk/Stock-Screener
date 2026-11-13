"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import Link from "next/link";
import { ReactNode, useEffect } from "react";
import { MarketPulse } from "@/components/market-pulse";
import { ScanBar } from "@/components/scan-bar";
import { Card, Empty, ErrorBox, SortTable, Ticker } from "@/components/ui";
import { useApi } from "@/lib/api";
import { compact, nf, pct, tone, usd } from "@/lib/format";
import type { Dashboard } from "@/lib/types";

type Col = { label: string; cell: (r: any) => ReactNode; sort?: (r: any) => number | string | null | undefined };

/** "Top 5" card summarising one tab, with a link to the full list. */
function Mini({ title, href, rows, cols, empty, loading }: { title: string; href: string; rows: any[] | undefined; cols: Col[]; empty: string; loading?: boolean }) {
  return (
    <Card title={title} right={<Link href={href} className="text-xs text-accent hover:underline">View all →</Link>}>
      {rows === undefined || (loading && !rows.length) ? <Empty>Loading…</Empty> : rows.length ? (
        <SortTable rows={rows} limit={5} rowKey={(r, i) => (r.symbol ?? r.ticker) + i}
          cols={[{ key: "ticker", label: "Ticker", sort: (r) => r.symbol ?? r.ticker, cell: (r) => <Ticker symbol={r.symbol ?? r.ticker} status={r.shariah_status} /> },
            // insider rows carry "price" = the insider's purchase price; their current price is "last_price"
            { key: "price", label: "Price", className: "num", sort: (r) => ("last_price" in r ? r.last_price : r.price), cell: (r) => usd("last_price" in r ? r.last_price : r.price) },
            ...cols.map((c) => ({ key: c.label, label: c.label, cell: c.cell, sort: c.sort, className: "num" }))]} />
      ) : <Empty>{empty}</Empty>}
    </Card>
  );
}

export default function DashboardPage() {
  const dash = useApi<Dashboard>("/dashboard");
  const run = dash.data?.latest_run;
  // After a CSV import starts a scan (here or on GitHub), check every 30 s until a newer scan has finished,
  // so Near-qualified and the Top 5 update by themselves.
  const waiting = (() => {
    if (run?.status === "running") return true;
    let asked: string | null = null;
    try { asked = localStorage.getItem("scanRequestedAt"); } catch { /* storage unavailable */ }
    if (!asked || Date.now() - Date.parse(asked) > 60 * 60e3) return false;  // give up after an hour
    return !run?.finished_at || Date.parse(run.started_at) < Date.parse(asked) - 5 * 60e3;
  })();
  const { reload } = dash;
  useEffect(() => {
    if (!waiting) return;
    const t = setInterval(reload, 30e3);
    return () => clearInterval(t);
  }, [waiting, reload]);
  const imports = useApi<{ id: number }[]>("/options-imports");
  const latest = imports.data?.[0];
  const opts = useApi<{ data: any[] }>(latest ? `/options-imports/${latest.id}` : null);
  // same rule as the Insider Buying tab: purchases above 1% of outstanding shares in the last 90 days
  const ins = useApi<{ rows: any[] }>("/insiders?min_pct=1");
  const earn = useApi<{ rows: any[] }>("/earnings/upcoming?days=7");
  const month = new Date().getMonth() + 1;
  const seas = useApi<{ month_name: string; ranked: any[] }>(`/seasonality?month=${month}&min_years=5&min_price=5&max_price=100&limit=5`);

  const optRows = opts.data ? opts.data.data.filter((r) => r.passes_rules).sort((a, b) => (b.vol_change_1m_pct ?? -1e9) - (a.vol_change_1m_pct ?? -1e9)) : latest === undefined && imports.data ? [] : undefined;
  const insRows = ins.data ? [...ins.data.rows].sort((a, b) => (b.pct_outstanding ?? 0) - (a.pct_outstanding ?? 0)) : undefined;
  const earnRows = earn.data ? [...earn.data.rows].sort((a, b) => (b.analysts?.strong_buy ?? -1) - (a.analysts?.strong_buy ?? -1)) : undefined;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <ScanBar title="Dashboard" onScanned={() => { dash.reload(); ins.reload(); }} />
      </div>
      <ErrorBox error={dash.error} />
      <MarketPulse />

      <Card title="Near-qualified — fails one or two rules" right={waiting ? <span className="text-xs text-accent">Scan with the new CSV in progress — updates automatically…</span> : undefined}>
        {dash.data?.watchlist.length ? (
          <SortTable rows={dash.data.watchlist} rowKey={(w) => w.ticker} initial="score"
            cols={[
              { key: "ticker", label: "Ticker", cell: (w) => <Ticker symbol={w.ticker} status={w.shariah_status} /> },
              { key: "company", label: "Company", cell: (w) => <span className="block max-w-56 truncate text-muted">{w.company}</span> },
              { key: "price", label: "Price", cell: (w) => usd(w.price), className: "num" },
              { key: "score", label: "Score", cell: (w) => nf(w.score, 1), className: "num" },
              { key: "fails", label: "Fails", sort: (w) => w.failures.length, cell: (w) => <span className="text-xs text-bear">{w.failures.join(", ")}</span> },
            ]} />
        ) : <Empty>No near-qualified stocks in the last scan.</Empty>}
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Mini title="Unusual options — top 5" href="/options" rows={optRows} empty="No stocks in the latest options CSV meet the criteria."
          cols={[{ label: "1M vol Δ", sort: (r) => r.vol_change_1m_pct, cell: (r) => <span className={tone(r.vol_change_1m_pct)}>{pct(r.vol_change_1m_pct, 0, true)}</span> },
            { label: "IV rank", cell: (r) => nf(r.iv_rank, 0), sort: (r) => r.iv_rank }, { label: "P/C", cell: (r) => nf(r.pc_ratio, 2), sort: (r) => r.pc_ratio }]} />
        <Mini title="Insider buying — top 5 by % of outstanding" href="/insiders" rows={insRows} empty="No insider purchases above 1% of outstanding shares."
          cols={[{ label: "Insider", sort: (r) => r.owner, cell: (r) => <span className="block max-w-36 truncate text-left text-muted">{r.owner}</span> },
            { label: "% outstanding", sort: (r) => r.pct_outstanding, cell: (r) => <span className="text-bull">{nf(r.pct_outstanding, 2)}%</span> },
            { label: "Value", cell: (r) => `$${compact(r.value)}`, sort: (r) => r.value }, { label: "Filed", sort: (r) => r.filing_date, cell: (r) => <span className="text-xs">{r.filing_date}</span> }]} />
        <Mini title="Upcoming earnings — top 5 by Strong Buy" href="/earnings" rows={earnRows} empty="No earnings in the next 7 days."
          cols={[{ label: "Date", cell: (r) => <span className="text-xs">{r.date}</span>, sort: (r) => r.date },
            { label: "Insider buying", sort: (r) => r.insider?.value, cell: (r) => r.insider ? <span className="text-bull">${compact(r.insider.value)}</span> : "—" },
            { label: "Options 1M Δ", sort: (r) => r.options?.vol_change_1m_pct, cell: (r) => r.options ? pct(r.options.vol_change_1m_pct, 0, true) : "—" },
            { label: "Strong Buy", sort: (r) => r.analysts?.strong_buy, cell: (r) => r.analysts?.strong_buy ?? "—" }]} />
        <Mini title={`Seasonality — best in ${seas.data?.month_name ?? ""}`} href="/seasonality" rows={seas.data?.ranked} empty="Import a price-history CSV on the Seasonality tab."
          cols={[{ label: "Avg return", sort: (r) => r.avg, cell: (r) => <span className={tone(r.avg)}>{pct(r.avg, 2, true)}</span> },
            { label: "% years up", cell: (r) => `${nf(r.win_rate, 0)}%`, sort: (r) => r.win_rate }, { label: "Years", cell: (r) => r.years, sort: (r) => r.years }]} />
      </div>
    </div>
  );
}
