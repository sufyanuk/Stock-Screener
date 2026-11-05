"use client";
import { useState } from "react";
import { Button, Card, Empty, ErrorBox, Field, ShariahLegend, Ticker } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { nf, pct, tone, usd } from "@/lib/format";
import { chunks, readOhlcv, type MonthEnd, type ParseStats } from "@/lib/ohlcv";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

type Entry = { rank: number; symbol: string; years: number; avg: number; median: number; win_rate: number; best: number; best_year: number;
  worst: number; worst_year: number; last_close: number | null; heatmap: (number | null)[]; shariah_status: string | null };
type Coverage = { first_month: string | null; last_month: string | null; months: number; symbols: number; provider: string | null; computed_at: string | null };
type Resp = { month: number; month_name: string; ranked: Entry[]; total_matching: number; note: string; coverage: Coverage;
  best_by_month: { month: number; month_name: string; top: { symbol: string; avg: number; win_rate: number; years: number; shariah_status?: string | null }[] }[] };

function heat(v: number | null) {
  if (v === null || Math.abs(v) < 0.05) return "bg-panel-2 text-muted";
  const a = Math.min(Math.abs(v) / 6, 1);
  return v >= 0 ? (a > 0.66 ? "bg-bull/60" : a > 0.33 ? "bg-bull/35" : "bg-bull/15") : (a > 0.66 ? "bg-bear/60" : a > 0.33 ? "bg-bear/35" : "bg-bear/15");
}

export default function SeasonalityPage() {
  const [month, setMonth] = useState(new Date().getMonth() + 1);
  const [minYears, setMinYears] = useState(5);
  const [rankBy, setRankBy] = useState("avg");
  const [minPrice, setMinPrice] = useState("5");
  const [maxPrice, setMaxPrice] = useState("100");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const qs = new URLSearchParams({ month: String(month), min_years: String(minYears), rank_by: rankBy, limit: "200" });
  if (minPrice) qs.set("min_price", minPrice);
  if (maxPrice) qs.set("max_price", maxPrice);
  if (q) qs.set("q", q);
  const { data, error, reload } = useApi<Resp>(`/seasonality?${qs}`);
  const cov = data?.coverage;

  const [files, setFiles] = useState<File[]>([]);
  const [parsed, setParsed] = useState<{ data: MonthEnd; stats: ParseStats } | null>(null);
  const [replaceAll, setReplaceAll] = useState(true);
  const [done, setDone] = useState<string | null>(null);

  async function read(list: File[]) {
    setFiles(list); setParsed(null); setErr(null); setDone(null);
    if (!list.length) return;
    setBusy("Reading…");
    try {
      const res = await readOhlcv(list, setBusy);
      if (!res.stats.used) throw new Error("No usable rows found (need Date and Close columns with prices).");
      setParsed(res);
    } catch (e) {
      setErr((e as Error).message);
    }
    setBusy(null);
  }

  async function upload() {
    if (!parsed) return;
    setErr(null); setDone(null);
    const source = files.length === 1 ? files[0].name : `${files.length} CSV files`;
    const parts = chunks(parsed.data);
    try {
      let stored = 0;
      for (let i = 0; i < parts.length; i++) {
        setBusy(`Uploading month-end prices… ${i + 1} of ${parts.length}`);
        const r = await api<{ stored: number }>("/seasonality/import", { method: "POST",
          body: JSON.stringify({ rows: parts[i], replace_all: replaceAll && i === 0, source }) });
        stored += r.stored;
      }
      setBusy("Calculating seasonality for every stock…");
      const f = await api<{ stats: number; partial_month_dropped: string | null }>("/seasonality/import/finish", {
        method: "POST", body: JSON.stringify({ source, last_date: parsed.stats.lastDate }) });
      setDone(`Imported ${stored.toLocaleString()} month-end prices for ${parsed.stats.symbols.toLocaleString()} stocks.` +
        (f.partial_month_dropped ? ` ${f.partial_month_dropped} was left out because the file ends part-way through that month.` : ""));
      setParsed(null); setFiles([]);
    } catch (e) {
      setErr((e as Error).message);
    }
    setBusy(null);
    reload();
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Seasonality</h1>
          <p className="text-sm text-muted">
            Ranked by how each stock performed in the chosen calendar month over past years, from your imported price-history CSV.
            {cov && cov.months > 0 && <> Data: {cov.symbols.toLocaleString()} stocks · {cov.first_month?.slice(0, 7)} → {cov.last_month?.slice(0, 7)} ({cov.months} months) · {cov.provider}</>}
          </p>
        </div>
        <ShariahLegend />
      </div>

      <Card title="Price history CSV">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <input type="file" accept=".csv,.txt,text/csv" multiple disabled={!!busy}
            onChange={(e) => read(Array.from(e.target.files ?? []))} />
          <label className="flex items-center gap-1.5"><input type="checkbox" checked={replaceAll} onChange={(e) => setReplaceAll(e.target.checked)} />
            Replace all existing price history</label>
          {parsed && <Button onClick={upload} disabled={!!busy}>Import</Button>}
        </div>
        {parsed && (
          <p className="mt-2 text-sm">
            Read {parsed.stats.used.toLocaleString()} rows: <b>{parsed.stats.symbols.toLocaleString()}</b> stocks,{" "}
            {parsed.stats.firstDate} → {parsed.stats.lastDate} ({parsed.stats.months} months), using the <b>{parsed.stats.closeColumn}</b> column,
            ticker from the {parsed.stats.symbolSource} (e.g. {parsed.stats.sample.slice(0, 5).join(", ")}).
            {parsed.stats.skipped > 0 && <span className="text-warn"> {parsed.stats.skipped.toLocaleString()} rows skipped (missing date/price).</span>}
          </p>
        )}
        {busy && <p className="mt-2 text-xs text-muted">{busy}</p>}
        {done && <p className="mt-2 text-sm text-bull">{done}</p>}
        <div className="mt-2"><ErrorBox error={err} /></div>
        <p className="mt-2 text-xs text-muted">
          Daily (or weekly/monthly) bars with a header row: Symbol/Ticker, Date, Open, High, Low, Close, Adj Close (optional), Volume —
          one file for all stocks, or one file per stock named by ticker (e.g. AAPL.csv). The file is read in your browser and only each
          stock&apos;s month-end close is uploaded. Use split-adjusted prices; “Adj Close” is used when present.
          {!replaceAll && " Unticked: stocks in the file replace their own history; other stocks are kept."}
        </p>
      </Card>

      <Card>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <Field label="Rank by"><select value={rankBy} onChange={(e) => setRankBy(e.target.value)}><option value="avg">Average return</option><option value="win">% of years positive</option></select></Field>
          <Field label="Minimum years of history"><input type="number" min={1} max={20} value={minYears} onChange={(e) => setMinYears(Number(e.target.value) || 1)} /></Field>
          <Field label="Price from ($)"><input value={minPrice} onChange={(e) => setMinPrice(e.target.value.replace(/[^0-9.]/g, ""))} placeholder="any" /></Field>
          <Field label="Price below ($)"><input value={maxPrice} onChange={(e) => setMaxPrice(e.target.value.replace(/[^0-9.]/g, ""))} placeholder="any" /></Field>
          <Field label="Ticker starts with"><input value={q} onChange={(e) => setQ(e.target.value.toUpperCase())} placeholder="e.g. AA" /></Field>
        </div>
        <div className="mt-3"><ErrorBox error={error} /></div>
        <div className="mt-3 flex flex-wrap gap-1">
          {MONTHS.map((m, i) => (
            <button key={m} onClick={() => setMonth(i + 1)}
              className={`rounded-md px-3 py-1.5 text-sm ${month === i + 1 ? "bg-accent text-bg" : "border border-line text-muted hover:text-ink"}`}>{m}</button>
          ))}
        </div>
      </Card>

      <Card title={data ? `Best stocks in ${data.month_name} — top ${data.ranked.length} of ${data.total_matching.toLocaleString()}` : "Ranking"}>
        {data?.ranked.length ? (
          <div className="overflow-x-auto">
            <table className="text-sm">
              <thead><tr><th>#</th><th>Ticker</th><th>Price</th><th>Avg return</th><th>Median</th><th>% years up</th><th>Years</th><th>Best</th><th>Worst</th>
                <th>Avg return by month (Jan → Dec)</th></tr></thead>
              <tbody>{data.ranked.map((e) => (
                <tr key={e.symbol}>
                  <td className="num">{e.rank}</td>
                  <td><Ticker symbol={e.symbol} status={e.shariah_status} /></td>
                  <td className="num">{usd(e.last_close)}</td>
                  <td className={`num font-semibold ${tone(e.avg)}`}>{pct(e.avg, 2, true)}</td>
                  <td className={`num ${tone(e.median)}`}>{pct(e.median, 2, true)}</td>
                  <td className="num">{nf(e.win_rate, 0)}%</td>
                  <td className="num">{e.years}</td>
                  <td className={`num ${tone(e.best)}`}>{pct(e.best, 1, true)} <span className="text-xs text-muted">{e.best_year}</span></td>
                  <td className={`num ${tone(e.worst)}`}>{pct(e.worst, 1, true)} <span className="text-xs text-muted">{e.worst_year}</span></td>
                  <td><div className="flex gap-0.5">{e.heatmap.map((v, i) => (
                    <span key={i} title={`${MONTHS[i]}: ${v === null ? "no data" : pct(v, 2, true)}`}
                      className={`grid h-5 w-7 place-items-center rounded text-[9px] ${heat(v)} ${i + 1 === month ? "ring-1 ring-accent" : ""}`}>{MONTHS[i][0]}</span>))}</div></td>
                </tr>))}</tbody>
            </table>
          </div>
        ) : <Empty>{cov?.months ? "No stocks match these filters." : "No price history yet — import your historical OHLCV CSV above."}</Empty>}
        <p className="mt-2 text-xs text-muted">{data?.note} Monthly moves beyond ±200% are treated as data errors and ignored.</p>
      </Card>

      <Card title="Top 5 for each month">
        {data?.best_by_month.some((m) => m.top.length) ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6">
            {data.best_by_month.map((m) => (
              <div key={m.month} className={`rounded-lg border p-2 ${m.month === month ? "border-accent" : "border-line"}`}>
                <button className="mb-1 text-sm font-semibold hover:text-accent" onClick={() => setMonth(m.month)}>{m.month_name}</button>
                {m.top.length ? m.top.map((t) => (
                  <div key={t.symbol} className="flex justify-between text-xs"><Ticker symbol={t.symbol} status={t.shariah_status} />
                    <span className={`num ${tone(t.avg)}`}>{pct(t.avg, 1, true)} <span className="text-muted">{nf(t.win_rate, 0)}%</span></span></div>
                )) : <div className="text-xs text-muted">—</div>}
              </div>
            ))}
          </div>
        ) : <Empty>Import the price history CSV to see the monthly leaders.</Empty>}
      </Card>
    </div>
  );
}
