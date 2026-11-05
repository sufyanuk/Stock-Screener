"use client";
import { ScanBar } from "@/components/scan-bar";
import { Top5 } from "@/components/top5";
import { Card, Empty, ErrorBox, Ticker } from "@/components/ui";
import { useApi } from "@/lib/api";
import { nf, usd } from "@/lib/format";
import type { Dashboard } from "@/lib/types";

export default function WatchlistPage() {
  const { data, error, reload } = useApi<Dashboard>("/dashboard", 60_000);
  const run = data?.latest_run;
  return (
    <div className="space-y-4">
      <ScanBar title="Watchlist" onScanned={reload} />
      <ErrorBox error={error} />
      <Card title="Top 5 — meets every rule" right={run && <span className="text-xs text-muted">Funnel: {run.funnel.stage1_evaluated ?? 0} under $100 → {run.funnel.stage1_options_pass ?? 0} options pass → {run.funnel.eligible ?? 0} fully eligible</span>}>
        {run ? <Top5 rows={data?.top ?? []} /> : <Empty>No completed scan yet — import today&apos;s options CSV and click “Run scan now”.</Empty>}
      </Card>
      <Card title="Near-qualified — fails one or two rules">
        {data?.watchlist.length ? (
          <div className="overflow-x-auto">
            <table className="text-sm">
              <thead><tr><th>Ticker</th><th>Company</th><th>Price</th><th>Score</th><th>Fails</th></tr></thead>
              <tbody>{data.watchlist.map((w) => (
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
    </div>
  );
}
