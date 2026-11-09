"use client";
/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState } from "react";
import { Badge, Button, Card, Empty, ErrorBox, Field, SortTable } from "@/components/ui";
import { api, useApi } from "@/lib/api";

type Batch = { id: number; filename: string; session_date: string; rows: number; percent_interpretation: string; imported_by: string; imported_at: string; summary: any };

export function OptionsImport({ isAdmin = true, open: openInit = true, onImported }: { isAdmin?: boolean; open?: boolean; onImported?: () => void }) {
  const list = useApi<Batch[]>("/options-imports");
  const [open, setOpen] = useState(openInit);
  const [file, setFile] = useState<File | null>(null);
  const [sessionDate, setSessionDate] = useState("");
  const [percent, setPercent] = useState("auto");
  const [result, setResult] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [scanMsg, setScanMsg] = useState<string | null>(null);

  async function scanNow() {
    // A new CSV changes the unusual-options criterion: rescan straight away so the Top 5 uses it.
    setScanMsg("Running a scan with the new CSV… this can take a few minutes.");
    try {
      const r = await api<{ queued: boolean; status?: string }>("/scan", { method: "POST" });
      setScanMsg(r.queued ? "Scan started — results appear when it finishes." : `Scan finished (${r.status}).`);
    } catch (e) {
      const m = (e as Error).message;
      setScanMsg(/already running/i.test(m) ? "A scan is already running — it will use the new CSV." : `Scan could not start: ${m}`);
    }
    onImported?.();
  }

  async function send(dryRun: boolean) {
    if (!file) return;
    setBusy(true); setErr(null);
    try {
      if (file.size > 4_000_000) throw new Error("File is larger than 4 MB. Export only the latest day's rows.");
      const csv = await file.text();
      const r = await api("/admin/options-import", {
        method: "POST",
        body: JSON.stringify({ filename: file.name, csv, percent_numbers: percent, session_date: sessionDate || null, dry_run: dryRun }),
      });
      setResult(r);
      if (!dryRun) { list.reload(); onImported?.(); void scanNow(); }
    } catch (e) {
      setErr((e as Error).message); setResult(null);
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number) {
    if (!confirm("Delete this import? Scans that already used it keep their stored results.")) return;
    await api(`/admin/options-import/${id}`, { method: "DELETE" });
    list.reload();
    onImported?.();
  }

  const s = result?.summary;
  return (
    <Card title={<button className="flex items-center gap-2" onClick={() => setOpen(!open)}><span className="text-muted">{open ? "▾" : "▸"}</span>Import today&apos;s unusual-options CSV</button>}
      right={list.data?.[0] && <span className="text-xs text-muted">In use: #{list.data[0].id} · {list.data[0].filename} · session {list.data[0].session_date}</span>}>
      {!open ? <p className="text-xs text-muted">Click to upload a new file or manage previous imports.</p> : <>
      <p className="mb-3 text-sm text-muted">
        Upload the unusual-options-volume export (Barchart format or your spreadsheet&apos;s UOV columns: Symbol, Name, Latest, Change, %Change,
        IV Rank, Options Vol, 1M Vol %Chg, Put Vol, Call Vol, P/C Vol, Date). Scans use the latest import; an import older than the
        last completed trading session is treated as missing, never as current.
      </p>
      {isAdmin ? (
        <>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
            <Field label="CSV file"><input type="file" accept=".csv,text/csv" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setResult(null); }} /></Field>
            <Field label="Session date (optional)" hint="Overrides the Date column / footer"><input type="date" value={sessionDate} onChange={(e) => setSessionDate(e.target.value)} /></Field>
            <Field label="Plain numbers in % columns" hint="Auto: values with % signs are percents, others fractions">
              <select value={percent} onChange={(e) => setPercent(e.target.value)}>
                <option value="auto">Auto-detect</option><option value="fraction">Fractions (0.2 = 20%)</option><option value="percent">Percents (20 = 20%)</option>
              </select>
            </Field>
            <div className="flex items-end gap-2">
              <Button variant="ghost" disabled={!file || busy} onClick={() => send(true)}>Preview</Button>
              <Button disabled={!file || busy} onClick={() => send(false)}>{busy ? "…" : "Import"}</Button>
            </div>
          </div>
          <div className="mt-3"><ErrorBox error={err} /></div>
          {scanMsg && <p className="mt-2 text-sm text-accent">{scanMsg}</p>}
          {s && (
            <div className="mt-3 space-y-2">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge tone={result.dry_run ? "info" : "bull"}>{result.dry_run ? "Preview — not saved" : `Imported as #${result.batch.id}`}</Badge>
                <span>{s.rows} rows · session {s.session_date} · read % columns as {s.percent_interpretation}</span>
                {s.skipped_count > 0 && <span className="text-warn">{s.skipped_count} skipped</span>}
              </div>
              {s.warnings?.length > 0 && <ul className="list-disc pl-5 text-xs text-warn">{s.warnings.map((w: string) => <li key={w}>{w}</li>)}</ul>}
              {result.dry_run && <p className="text-xs text-muted">Preview only — click Import to save it. Stocks that meet the unusual-options criteria appear in the table below.</p>}
            </div>
          )}
        </>
      ) : <p className="text-sm text-warn">Only an administrator can import files.</p>}
      <h3 className="mb-1 mt-5 text-sm font-semibold">Recent imports</h3>
      {list.data?.length ? (
        <SortTable rows={list.data} rowKey={(b) => String(b.id)} initial="id"
          cols={[
            { key: "id", label: "#", cell: (b) => <>{b.id}{b.id === list.data![0].id && <> <Badge tone="info">latest</Badge></>}</> },
            { key: "filename", label: "File", cell: (b) => <span className="block max-w-48 truncate">{b.filename}</span> },
            { key: "session_date", label: "Session", cell: (b) => b.session_date },
            { key: "rows", label: "Rows", cell: (b) => b.rows, className: "num" },
            { key: "percent_interpretation", label: "% read as", cell: (b) => <span className="text-xs text-muted">{b.percent_interpretation}</span> },
            { key: "imported_by", label: "By", cell: (b) => <span className="text-xs">{b.imported_by}</span> },
            { key: "imported_at", label: "Imported", cell: (b) => <span className="text-xs text-muted">{b.imported_at.slice(0, 16).replace("T", " ")}</span> },
            { key: "actions", label: "", sort: () => null, cell: (b) => isAdmin && <Button variant="danger" onClick={() => remove(b.id)}>Delete</Button> },
          ]} />
      ) : <Empty>No imports yet.</Empty>}
      </>}
    </Card>
  );
}
