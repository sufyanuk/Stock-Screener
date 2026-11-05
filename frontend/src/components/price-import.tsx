"use client";
/** Price-history (OHLCV) CSV import for the Seasonality tab (month-end closes per stock). */
import { useState } from "react";
import { Button, Card, ErrorBox } from "@/components/ui";
import { api } from "@/lib/api";
import { chunks, readOhlcv, type MonthEnd, type ParseStats, type Recent } from "@/lib/ohlcv";

export function PriceImport({ onDone, open: openInit = true }: { onDone?: () => void; open?: boolean }) {
  const [open, setOpen] = useState(openInit);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [parsed, setParsed] = useState<{ data: MonthEnd; recent: Recent; stats: ParseStats } | null>(null);
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
    onDone?.();
  }

  return (
      <Card title={<button className="flex items-center gap-2" onClick={() => setOpen(!open)}><span className="text-muted">{open ? "▾" : "▸"}</span>Import price-history CSV (OHLCV)</button>}>
        {!open ? <p className="text-xs text-muted">Click to import a new price-history file for the seasonality analysis.</p> : <>
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
          stock&apos;s month-end closes are uploaded. Use split-adjusted prices; “Adj Close” is used when present.
          {!replaceAll && " Unticked: stocks in the file replace their own history; other stocks are kept."}
        </p>
      </>}
      </Card>
  );
}
