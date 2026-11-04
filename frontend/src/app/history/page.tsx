"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import Link from "next/link";
import { Badge, Card, Empty, ErrorBox, Stat, useSort } from "@/components/ui";
import { useApi } from "@/lib/api";
import { nf, pct, tone, usd } from "@/lib/format";

export default function HistoryPage() {
  const { data, error } = useApi<any[]>("/recommendations/history?days=365");
  const rows = data ?? [];
  const { sorted, th } = useSort(rows, "session_date");
  const withRet = rows.filter((r) => r.return_pct !== null);
  const wins = withRet.filter((r) => r.return_pct > 0).length;
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Historical recommendations</h1>
      <p className="text-sm text-muted">Final (closing-scan) Top 5 lists. Records are immutable; corrections appear as dated revisions.</p>
      <ErrorBox error={error} />
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Stat label="Recommendations" value={rows.length} />
        <Stat label="Positive since rec." value={withRet.length ? pct((wins / withRet.length) * 100, 1) : "—"} />
        <Stat label="Hit Target 1" value={rows.filter((r) => r.hit_target1).length} />
        <Stat label="Hit stop" value={rows.filter((r) => r.hit_stop).length} />
      </div>
      <Card>
        {rows.length ? (
          <div className="overflow-x-auto"><table className="text-sm"><thead><tr>
            {th("session_date", "Date")}{th("rank", "#")}{th("ticker", "Ticker")}{th("score", "Score")}{th("price_at_rec", "Price at rec.")}<th>Entry</th><th>Stop</th><th>T1</th>
            {th("last_price", "Last close")}{th("return_pct", "Return")}<th>Outcome</th><th>Revisions</th></tr></thead>
            <tbody>{sorted.map((r) => (
              <tr key={r.id}><td>{r.session_date}</td><td>{r.rank}</td><td><Link className="text-accent" href={`/stock/${r.ticker}`}>{r.ticker}</Link></td>
                <td className="num">{nf(r.score, 1)}</td><td className="num">{usd(r.price_at_rec)}</td><td className="num">{usd(r.entry)}</td><td className="num">{usd(r.stop)}</td><td className="num">{usd(r.target1)}</td>
                <td className="num">{usd(r.last_price)} <span className="text-xs text-muted">{r.last_session}</span></td>
                <td className={`num ${tone(r.return_pct)}`}>{pct(r.return_pct, 2, true)}</td>
                <td>{r.hit_target1 ? <Badge tone="bull">T1 hit</Badge> : r.hit_stop ? <Badge tone="bear">stopped</Badge> : <Badge>open</Badge>}</td>
                <td className="text-xs text-muted">{r.revisions.length ? r.revisions.map((v: any) => `${v.changed_at.slice(0, 10)} ${v.changed_by}: ${v.reason}`).join("; ") : "—"}</td></tr>))}</tbody></table></div>
        ) : <Empty>No final recommendations recorded yet.</Empty>}
      </Card>
    </div>
  );
}
