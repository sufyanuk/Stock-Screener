"use client";
import { useEffect, useRef, useState } from "react";
import { Badge, Button, Card, Empty, ErrorBox, ShariahLegend, Ticker, useSort } from "@/components/ui";
import { api, JOBS_ON_GITHUB, useApi } from "@/lib/api";
import { compact, nf, pct } from "@/lib/format";
import { daysUntil } from "@/lib/pane-context";

type Row = {
  symbol: string; name: string | null; date: string; time_of_day: string | null; eps_estimate: number | null; shariah_status: string | null;
  insider: { purchases: number; shares: number; value: number; latest: string; insiders: number } | null;
  options: { vol_change_1m_pct: number | null; options_volume: number | null; iv_rank: number | null; put_call: number | null } | null;
  analysts: { strong_buy: number | null; buy: number | null; hold: number | null; sell: number | null; strong_sell: number | null } | null;
};
type Resp = {
  rows: Row[]; earnings_source: string | null; analysts_missing: number; insider_window_days: number;
  options_import: { id: number; filename: string; session_date: string } | null; refresh: { error?: string };
};

export default function EarningsPage() {
  const { data, error, reload, loading } = useApi<Resp>("/earnings/upcoming?days=7");
  const [onlyInsider, setOnlyInsider] = useState(false);
  const [onlyOptions, setOnlyOptions] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  async function loadAnalysts() {
    setBusy("Loading analyst counts…");
    try {
      for (let i = 0; i < 10; i++) {
        const r = await api<{ fetched: number; remaining: number; errors: string[] }>("/earnings/load-analysts?days=7&limit=100", { method: "POST" });
        setBusy(`Loaded ${r.fetched}; ${r.remaining} remaining…`);
        if (r.remaining === 0 || r.fetched === 0) {
          setBusy(r.errors.length && r.fetched === 0 ? `Could not load: ${r.errors[0]}` : null);
          break;
        }
      }
    } catch (e) {
      setBusy((e as Error).message);
    }
    reload();
  }

  // Analyst counts load automatically the first time the screen opens with companies missing them.
  const auto = useRef(false);
  useEffect(() => {
    if (data && data.analysts_missing > 0 && !auto.current && !JOBS_ON_GITHUB) {
      auto.current = true;
      loadAnalysts();
    }
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  const rows = (data?.rows ?? []).filter((r) => (!onlyInsider || r.insider) && (!onlyOptions || r.options))
    .map((r) => ({ ...r, ins_value: r.insider?.value ?? null, opt_chg: r.options?.vol_change_1m_pct ?? null, strong_buy: r.analysts?.strong_buy ?? null,
      buy: r.analysts?.buy ?? null, iv_rank: r.options?.iv_rank ?? null, put_call: r.options?.put_call ?? null,
      analysts_total: r.analysts ? ["strong_buy", "buy", "hold", "sell", "strong_sell"].reduce((a, k) => a + ((r.analysts as Record<string, number | null>)[k] ?? 0), 0) : null }));
  // Most "Strong Buy" analyst ratings first (companies without counts yet go last, soonest first).
  const { sorted, th } = useSort(rows, "strong_buy" as keyof (typeof rows)[number], true);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Upcoming earnings — next 7 days</h1>
          <p className="text-sm text-muted">
            Calendar: {data?.earnings_source ?? "—"} · insider buying = open-market purchases in the last {data?.insider_window_days ?? 90} days (SEC Form 4) ·
            unusual options = {data?.options_import ? `CSV import #${data.options_import.id} (${data.options_import.session_date})` : "no CSV imported"}
          </p>
        </div>
        <ShariahLegend />
      </div>
      <ErrorBox error={error || data?.refresh?.error || null} />
      <Card title={`${rows.length} companies reporting`} right={
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <label className="flex items-center gap-1.5"><input type="checkbox" checked={onlyInsider} onChange={(e) => setOnlyInsider(e.target.checked)} /> With insider buying</label>
          <label className="flex items-center gap-1.5"><input type="checkbox" checked={onlyOptions} onChange={(e) => setOnlyOptions(e.target.checked)} /> With unusual options</label>
          {data && data.analysts_missing > 0 && !JOBS_ON_GITHUB && <Button variant="ghost" onClick={loadAnalysts} disabled={!!busy}>Load analyst counts ({data.analysts_missing})</Button>}
          {busy && <span className="text-xs text-muted">{busy}</span>}
        </div>}>
        {loading && !data ? <Empty>Loading the earnings calendar…</Empty> : sorted.length ? (
          <div className="overflow-x-auto">
            <table className="text-sm">
              <thead><tr>
                {th("date", "Date")}{th("time_of_day", "Time")}{th("symbol", "Ticker")}{th("name", "Company")}{th("eps_estimate", "EPS est.")}
                {th("ins_value", "Insider buying")}{th("opt_chg", "Unusual options (1M vol Δ)")}{th("iv_rank", "IV rank")}{th("put_call", "P/C")}
                {th("strong_buy", "No. of analysts: Strong Buy")}{th("buy", "Buy")}{th("analysts_total", "Total analysts")}
              </tr></thead>
              <tbody>{sorted.map((r) => {
                const n = daysUntil(r.date);
                return (
                  <tr key={r.symbol + r.date}>
                    <td className="num">{r.date} <span className="text-xs text-muted">{n <= 0 ? "today" : n === 1 ? "tomorrow" : `${n}d`}</span></td>
                    <td className="text-xs text-muted">{r.time_of_day?.replace("-", " ") ?? "—"}</td>
                    <td><Ticker symbol={r.symbol} status={r.shariah_status} /></td>
                    <td className="max-w-56 truncate text-muted">{r.name}</td>
                    <td className="num">{nf(r.eps_estimate, 2)}</td>
                    <td className="num">{r.insider ? <span className="text-bull" title={`${r.insider.purchases} purchase(s) by ${r.insider.insiders} insider(s), latest ${r.insider.latest}`}>
                      ${compact(r.insider.value)} <span className="text-xs text-muted">({compact(r.insider.shares)} sh)</span></span> : <span className="text-muted">—</span>}</td>
                    <td className="num">{r.options ? <span className="text-bull">{pct(r.options.vol_change_1m_pct, 0, true)} <span className="text-xs text-muted">vol {compact(r.options.options_volume)}</span></span> : <span className="text-muted">—</span>}</td>
                    <td className="num">{nf(r.options?.iv_rank, 0)}</td>
                    <td className="num">{nf(r.options?.put_call, 2)}</td>
                    <td className="num">{r.analysts ? <Badge tone={(r.analysts.strong_buy ?? 0) > 0 ? "bull" : "muted"}>{r.analysts.strong_buy ?? 0}</Badge> : <span className="text-muted">—</span>}</td>
                    <td className="num">{r.analysts?.buy ?? "—"}</td>
                    <td className="num">{r.analysts_total ?? "—"}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        ) : <Empty>No earnings in the next 7 days{onlyInsider || onlyOptions ? " match the filters" : " — check that FMP_API_KEY or ALPHAVANTAGE_API_KEY is set"}.</Empty>}
        <p className="mt-2 text-xs text-muted">— = no data. Sorted by the number of analysts rating Strong Buy (click a column heading to re-sort). Analyst counts (Finnhub; FMP / Alpha Vantage as fallback) load automatically, soonest reports first — one request per company — and are cached for a day.</p>
      </Card>
    </div>
  );
}
