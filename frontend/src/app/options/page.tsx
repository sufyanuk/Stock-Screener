"use client";
import { useState } from "react";
import { CriteriaCard } from "@/components/criteria";
import { OptionsImport } from "@/components/options-import";
import { ScanBar } from "@/components/scan-bar";
import { Card, Empty, ErrorBox, Ticker, useSort } from "@/components/ui";
import { useApi } from "@/lib/api";
import { compact, nf, pct, tone, usd } from "@/lib/format";

type Row = {
  symbol: string; name: string | null; price: number | null; pct_change: number | null; iv_rank: number | null; options_volume: number | null;
  vol_change_1m_pct: number | null; put_volume: number | null; call_volume: number | null; pc_ratio: number | null;
  shariah_status: string | null; passes_rules: boolean; in_top5: boolean;
};
type Batch = { id: number; filename: string; session_date: string; rows: number };
type Detail = Batch & { data: Row[]; thresholds: { min_volume_change_pct: number; min_iv_rank: number; max_put_call_ratio: number; max_price: number } };

export default function OptionsPage() {
  const list = useApi<Batch[]>("/options-imports");
  const fresh = !!list.data?.[0];
  const latest = list.data?.[0];
  const { data, error, reload } = useApi<Detail>(latest ? `/options-imports/${latest.id}` : null);
  const [q, setQ] = useState("");
  const passing = (data?.data ?? []).filter((r) => r.passes_rules);
  const rows = passing.filter((r) => (!q || r.symbol.includes(q.toUpperCase()) || (r.name ?? "").toLowerCase().includes(q.toLowerCase())));
  const { sorted, th } = useSort(rows, "vol_change_1m_pct" as keyof Row, true);
  const t = data?.thresholds;
  return (
    <div className="space-y-4">
      <ScanBar showButton title="Unusual options activity" subtitle={latest ? `CSV import #${latest.id} · ${latest.filename} · session ${latest.session_date}` : undefined} onScanned={reload} />
      <ErrorBox error={error || list.error} />
      {list.data && <OptionsImport open={!fresh} onImported={() => { list.reload(); reload(); }} />}
      <CriteriaCard title="Unusual-options criteria" tab="Unusual Options" sections={["options"]} />
      <Card title={data ? `${passing.length} stock${passing.length === 1 ? "" : "s"} meet the unusual-options criteria (of ${data.data.length} imported)` : "Unusual options"} right={
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by ticker or name" className="w-48" />
        </div>}>
        {!latest && !list.loading ? (
          <Empty>No CSV imported yet — upload today&apos;s file above.</Empty>
        ) : sorted.length ? (
          <div className="overflow-x-auto">
            <table className="text-sm">
              <thead><tr>
                {th("symbol", "Ticker")}{th("name", "Company")}{th("price", "Price")}{th("pct_change", "Day")}
                {th("vol_change_1m_pct", "1M vol Δ")}{th("options_volume", "Options vol")}{th("call_volume", "Calls")}{th("put_volume", "Puts")}
                {th("pc_ratio", "P/C")}{th("iv_rank", "IV rank")}
              </tr></thead>
              <tbody>{sorted.map((r) => (
                <tr key={r.symbol}>
                  <td><Ticker symbol={r.symbol} status={r.shariah_status} /></td>
                  <td className="max-w-56 truncate text-muted">{r.name}</td>
                  <td className="num">{usd(r.price)}</td>
                  <td className={`num ${tone(r.pct_change)}`}>{pct(r.pct_change, 2, true)}</td>
                  <td className={`num ${tone(r.vol_change_1m_pct)}`}>{pct(r.vol_change_1m_pct, 0, true)}</td>
                  <td className="num">{compact(r.options_volume)}</td>
                  <td className="num">{compact(r.call_volume)}</td>
                  <td className="num">{compact(r.put_volume)}</td>
                  <td className="num">{nf(r.pc_ratio, 3)}</td>
                  <td className="num">{nf(r.iv_rank, 1)}</td>
                </tr>))}</tbody>
            </table>
          </div>
        ) : <Empty>{!data ? "Loading…" : q ? "No matching stocks." : "No stocks in the latest file meet the criteria."}</Empty>}
        {t && <p className="mt-2 text-xs text-muted">Shown: price below ${t.max_price}, 1M volume change ≥ {t.min_volume_change_pct}%, IV rank &gt; {t.min_iv_rank}, put/call &lt; {t.max_put_call_ratio} (criteria above). Large call volume is not automatically bullish.</p>}
      </Card>
    </div>
  );
}
