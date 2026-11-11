"use client";
import { useEffect, useRef, useState } from "react";
import { CriteriaCard } from "@/components/criteria";
import { ScanBar } from "@/components/scan-bar";
import { Card, Empty, ErrorBox, Ticker, useSort } from "@/components/ui";
import { api, JOBS_ON_GITHUB, useApi } from "@/lib/api";
import { compact, nf, usd } from "@/lib/format";

type Row = {
  ticker: string; owner: string; co_reporters: string[]; title: string; transaction_date: string; filing_date: string;
  shares: number; price: number | null; last_price: number | null; last_price_as_of?: string | null; value: number; owned_after: number | null; accession: string; shariah_status: string | null;
  outstanding_shares: number | null; pct_outstanding: number | null;
};
type Resp = { top: number; min_pct: number; rows: Row[]; total_purchases: number; shares_missing: number; shares_unavailable: number;
  latest_filing_date: string | null; source: string; url: string; insider_source?: string; matching?: number; window_days?: number; hidden_otc?: number; pulled_at: string | null; refresh_error: string | null;
  screener_rows: number; hidden_non_compliant: number; merged_joint: number };

export default function InsidersPage() {
  // The latest 100 open-market purchases from SEC Form 4 filings, collected by the nightly GitHub job.
  const [onlyBig, setOnlyBig] = useState(true);
  const { data, error, reload } = useApi<Resp>(`/insiders?top=100&min_pct=${onlyBig ? 1 : 0}`);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  // Shares outstanding are looked up automatically (cached 30 days) for purchases that lack them.
  const tried = useRef(false);
  useEffect(() => {
    const need = data ? data.shares_missing + data.shares_unavailable : 0;  // never looked up, or not found last time
    if (!data || need === 0 || tried.current || JOBS_ON_GITHUB) return;
    tried.current = true;
    (async () => {
      setBusy(`Looking up shares outstanding for ${need} stocks…`);
      try {
        let idle = 0;
        for (let i = 0; i < 10; i++) {
          const r = await api<{ fetched: number; remaining: number }>("/insiders/load-shares", { method: "POST" });
          if ((r as { skipped?: boolean }).skipped) break;  // loads run on GitHub: stop at once
          idle = r.fetched === 0 ? idle + 1 : 0;
          if (r.remaining === 0 || idle >= 2) break;
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
      <Card title={data ? (onlyBig ? `${data.matching ?? rows.length} purchase${(data.matching ?? rows.length) === 1 ? "" : "s"} above 1% of outstanding shares (last ${data.window_days ?? 90} days)${(data.matching ?? 0) > rows.length ? ` · newest ${rows.length} shown` : ""}`
          : `Latest ${rows.length} of ${data.total_purchases} insider purchases (last ${data.window_days ?? 90} days)`) : "Insider purchases"} right={
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <label className="flex items-center gap-1.5"><input type="checkbox" checked={onlyBig} onChange={(e) => setOnlyBig(e.target.checked)} />Only above 1% of outstanding shares</label>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by ticker or insider" className="w-48" />
        </div>}>
        {sorted.length ? (
          <div className="overflow-x-auto">
            <table className="text-sm">
              <thead><tr>
                {th("ticker", "Ticker")}{th("last_price", "Price")}{th("owner", "Insider")}{th("title", "Role")}{th("transaction_date", "Trade")}{th("filing_date", "Filed")}
                {th("shares", "Shares")}{th("outstanding_shares", "Outstanding shares")}{th("pct_outstanding", "% of outstanding")}{th("price", "Buy price")}{th("value", "Value")}{th("owned_after", "Owned after")}{th("accession", "Filing")}
              </tr></thead>
              <tbody>{sorted.map((b) => (
                <tr key={b.accession + b.owner + b.transaction_date}>
                  <td><Ticker symbol={b.ticker} status={b.shariah_status} /></td>
                  <td className="num" title={b.last_price_as_of ? `Close on ${b.last_price_as_of}` : undefined}>{usd(b.last_price)}</td>
                  <td className="max-w-52 truncate" title={b.co_reporters.length ? `Also reported by: ${b.co_reporters.join(", ")}` : undefined}>
                    {b.owner}{b.co_reporters.length ? <span className="text-muted"> (+{b.co_reporters.length} joint)</span> : null}</td>
                  <td className="max-w-40 truncate text-muted">{b.title}</td>
                  <td>{b.transaction_date}</td><td>{b.filing_date}</td>
                  <td className="num">{compact(b.shares)}</td><td className="num">{compact(b.outstanding_shares)}</td>
                  <td className={`num ${b.pct_outstanding != null && b.pct_outstanding > 1 ? "font-semibold text-bull" : ""}`}>{b.pct_outstanding == null ? "—" : `${nf(b.pct_outstanding, 2)}%`}</td><td className="num">{usd(b.price)}</td>
                  <td className="num text-bull">${compact(b.value)}</td><td className="num">{compact(b.owned_after)}</td>
                  <td><a className="text-xs text-accent hover:underline" target="_blank" rel="noopener noreferrer"
                    href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${encodeURIComponent(b.ticker)}&type=4&dateb=&owner=include&count=40`}>SEC</a></td>
                </tr>))}</tbody>
            </table>
          </div>
        ) : <Empty>{!data ? "Loading…" : data.total_purchases === 0
          ? `No purchases collected yet — the nightly job fills this from SEC filings${data.refresh_error ? ` (${data.refresh_error})` : ""}.`
          : `None of the ${data.total_purchases} purchases is above ${data.min_pct}% of shares outstanding${data.shares_missing ? " (still looking up share counts…)" : ""}.`}</Empty>}
        {busy && <p className="mt-2 text-xs text-muted">{busy}</p>}
        {data && <p className="mt-2 text-xs text-muted">
          {data.insider_source === "openinsider" ? <>Source: the top 100 results of <a className="text-accent hover:underline" href={data.url} target="_blank" rel="noopener noreferrer">this OpenInsider screener</a>
            {data.pulled_at ? ` (pulled ${data.pulled_at.slice(0, 16).replace("T", " ")} UTC)` : ""}.</>
            : <>Source: SEC EDGAR Form 4 filings, read every night by the GitHub data job (purchases filed in the last {data.window_days ?? 90} days, newest first; the lots of one filing are added together).</>}
          {` ${data.merged_joint} joint filings of the same purchase merged, ${data.hidden_non_compliant} Shariah non-compliant hidden${data.hidden_otc ? `, ${data.hidden_otc} OTC / unlisted stocks left out` : ""}.`}
          {onlyBig && ` Showing ${rows.length} of ${data.total_purchases} purchases above 1% of shares outstanding.`}
          {onlyBig && data.shares_unavailable > 0 && ` ${data.shares_unavailable} stock(s) have no shares-outstanding figure and are not shown.`}
          {data.refresh_error && <span className="text-warn"> {data.refresh_error}</span>}</p>}
        <p className="mt-2 text-xs text-muted">Only genuine open-market purchases (Form 4 code P). Grants, option exercises, gifts and sales are excluded; joint filings of one purchase are shown once.</p>
      </Card>
    </div>
  );
}
