"use client";
import { useEffect, useRef, useState } from "react";
import { CriteriaCard } from "@/components/criteria";
import { ScanBar } from "@/components/scan-bar";
import { Card, Empty, ErrorBox, Ticker, useSort } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { compact, nf, usd } from "@/lib/format";

type Row = {
  ticker: string; owner: string; co_reporters: string[]; title: string; transaction_date: string; filing_date: string;
  shares: number; price: number | null; value: number; owned_after: number | null; accession: string; shariah_status: string | null;
  outstanding_shares: number | null; pct_outstanding: number | null;
};
type Resp = { days: number; min_pct: number; rows: Row[]; total_purchases: number; shares_missing: number; shares_unavailable: number;
  latest_filing_date: string | null; source: string };

export default function InsidersPage() {
  const days = 90; // everything ingested (the scan keeps the last 90 days)
  const { data, error, reload } = useApi<Resp>(`/insiders?days=${days}`);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  // Shares outstanding are looked up automatically (cached 30 days) for purchases that lack them.
  const tried = useRef(false);
  useEffect(() => {
    if (!data || data.shares_missing === 0 || tried.current) return;
    tried.current = true;
    (async () => {
      setBusy(`Looking up shares outstanding for ${data.shares_missing} stocks…`);
      try {
        for (let i = 0; i < 6; i++) {
          const r = await api<{ fetched: number; remaining: number }>("/insiders/load-shares", { method: "POST" });
          if (r.remaining === 0 || r.fetched === 0) break;
        }
      } catch { /* shown as missing */ }
      setBusy(null);
      reload();
    })();
  }, [data, reload]);
  const rows = (data?.rows ?? []).filter((r) => !q || r.ticker.includes(q.toUpperCase()) || r.owner.toLowerCase().includes(q.toLowerCase()));
  const { sorted, th } = useSort(rows, "filing_date" as keyof Row, true);
  return (
    <div className="space-y-4">
      <ScanBar title="Latest insider buying" subtitle={data ? `${data.source}${data.latest_filing_date ? ` · latest filing ${data.latest_filing_date}` : ""}` : undefined} onScanned={reload} />
      <ErrorBox error={error} />
      <CriteriaCard title="Insider-buying criteria (used for the Top 5)" tab="Insider Buying" sections={["insider"]} />
      <Card title={`${rows.length} purchases above ${data?.min_pct ?? 1}% of outstanding shares · last ${days} days`} right={
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by ticker or insider" className="w-48" />
        </div>}>
        {sorted.length ? (
          <div className="overflow-x-auto">
            <table className="text-sm">
              <thead><tr>
                {th("ticker", "Ticker")}{th("owner", "Insider")}<th>Role</th>{th("transaction_date", "Trade")}{th("filing_date", "Filed")}
                {th("shares", "Shares")}{th("outstanding_shares", "Outstanding shares")}{th("pct_outstanding", "% of outstanding")}{th("price", "Price")}{th("value", "Value")}{th("owned_after", "Owned after")}<th>Filing</th>
              </tr></thead>
              <tbody>{sorted.map((b) => (
                <tr key={b.accession + b.owner + b.transaction_date}>
                  <td><Ticker symbol={b.ticker} status={b.shariah_status} /></td>
                  <td className="max-w-52 truncate" title={b.co_reporters.length ? `Also reported by: ${b.co_reporters.join(", ")}` : undefined}>
                    {b.owner}{b.co_reporters.length ? <span className="text-muted"> (+{b.co_reporters.length} joint)</span> : null}</td>
                  <td className="max-w-40 truncate text-muted">{b.title}</td>
                  <td>{b.transaction_date}</td><td>{b.filing_date}</td>
                  <td className="num">{compact(b.shares)}</td><td className="num">{compact(b.outstanding_shares)}</td>
                  <td className="num font-semibold text-bull">{nf(b.pct_outstanding, 2)}%</td><td className="num">{usd(b.price)}</td>
                  <td className="num text-bull">${compact(b.value)}</td><td className="num">{compact(b.owned_after)}</td>
                  <td><a className="text-xs text-accent hover:underline" target="_blank" rel="noopener noreferrer"
                    href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${encodeURIComponent(b.ticker)}&type=4&dateb=&owner=include&count=40`}>SEC</a></td>
                </tr>))}</tbody>
            </table>
          </div>
        ) : <Empty>{!data ? "Loading…" : data.total_purchases === 0
          ? `No open-market purchases filed in the last ${days} days have been loaded yet — click “Run scan now”.`
          : `None of the ${data.total_purchases} purchases is above ${data.min_pct}% of shares outstanding${data.shares_missing ? " (still looking up share counts…)" : ""}.`}</Empty>}
        {busy && <p className="mt-2 text-xs text-muted">{busy}</p>}
        {data && <p className="mt-2 text-xs text-muted">Showing purchases where shares bought ÷ shares outstanding is above {data.min_pct}% — {rows.length} of {data.total_purchases} purchases.
          {data.shares_unavailable > 0 && ` ${data.shares_unavailable} stock(s) have no shares-outstanding figure from the data APIs and are not shown.`}</p>}
        <p className="mt-2 text-xs text-muted">Only genuine open-market purchases (Form 4 code P). Grants, option exercises, gifts and sales are excluded; joint filings of one purchase are shown once.</p>
      </Card>
    </div>
  );
}
