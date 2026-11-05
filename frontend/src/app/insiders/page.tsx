"use client";
import { useState } from "react";
import { CriteriaCard } from "@/components/criteria";
import { ScanBar } from "@/components/scan-bar";
import { Card, Empty, ErrorBox, Ticker, useSort } from "@/components/ui";
import { useApi } from "@/lib/api";
import { compact, usd } from "@/lib/format";

type Row = {
  ticker: string; owner: string; co_reporters: string[]; title: string; transaction_date: string; filing_date: string;
  shares: number; price: number | null; value: number; owned_after: number | null; accession: string; shariah_status: string | null;
};
type Resp = { days: number; rows: Row[]; latest_filing_date: string | null; source: string };

export default function InsidersPage() {
  const [days, setDays] = useState(30);
  const { data, error, reload } = useApi<Resp>(`/insiders?days=${days}`);
  const [q, setQ] = useState("");
  const rows = (data?.rows ?? []).filter((r) => !q || r.ticker.includes(q.toUpperCase()) || r.owner.toLowerCase().includes(q.toLowerCase()));
  const { sorted, th } = useSort(rows, "filing_date" as keyof Row, true);
  return (
    <div className="space-y-4">
      <ScanBar title="Latest insider buying" subtitle={data ? `${data.source}${data.latest_filing_date ? ` · latest filing ${data.latest_filing_date}` : ""}` : undefined} onScanned={reload} />
      <ErrorBox error={error} />
      <CriteriaCard title="Insider-buying criteria (used for the Top 5)" tab="Insider Buying" sections={["insider"]} />
      <Card title={`${rows.length} open-market purchases`} right={
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by ticker or insider" className="w-48" />
          <div className="flex gap-1">{[7, 30, 60, 90].map((d) => (
            <button key={d} onClick={() => setDays(d)} className={`rounded-md px-2.5 py-1 ${days === d ? "bg-accent text-bg" : "border border-line text-muted hover:text-ink"}`}>{d}d</button>))}</div>
        </div>}>
        {sorted.length ? (
          <div className="overflow-x-auto">
            <table className="text-sm">
              <thead><tr>
                {th("ticker", "Ticker")}{th("owner", "Insider")}<th>Role</th>{th("transaction_date", "Trade")}{th("filing_date", "Filed")}
                {th("shares", "Shares")}{th("price", "Price")}{th("value", "Value")}{th("owned_after", "Owned after")}<th>Filing</th>
              </tr></thead>
              <tbody>{sorted.map((b) => (
                <tr key={b.accession + b.owner + b.transaction_date}>
                  <td><Ticker symbol={b.ticker} status={b.shariah_status} /></td>
                  <td className="max-w-52 truncate" title={b.co_reporters.length ? `Also reported by: ${b.co_reporters.join(", ")}` : undefined}>
                    {b.owner}{b.co_reporters.length ? <span className="text-muted"> (+{b.co_reporters.length} joint)</span> : null}</td>
                  <td className="max-w-40 truncate text-muted">{b.title}</td>
                  <td>{b.transaction_date}</td><td>{b.filing_date}</td>
                  <td className="num">{compact(b.shares)}</td><td className="num">{usd(b.price)}</td>
                  <td className="num text-bull">${compact(b.value)}</td><td className="num">{compact(b.owned_after)}</td>
                  <td><a className="text-xs text-accent hover:underline" target="_blank" rel="noopener noreferrer"
                    href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${encodeURIComponent(b.ticker)}&type=4&dateb=&owner=include&count=40`}>SEC</a></td>
                </tr>))}</tbody>
            </table>
          </div>
        ) : <Empty>{data ? `No open-market purchases filed in the last ${days} days have been ingested. Insider data comes from the OpenInsider screener (SEC EDGAR as fallback) — click “Run scan now” to load it.` : "Loading…"}</Empty>}
        <p className="mt-2 text-xs text-muted">Only genuine open-market purchases (Form 4 code P). Grants, option exercises, gifts and sales are excluded; joint filings of one purchase are shown once.</p>
      </Card>
    </div>
  );
}
