"use client";
import Link from "next/link";
import { ReactNode, useState } from "react";
import { Badge, Button, ErrorBox, ShariahLegend } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { ago } from "@/lib/format";
import type { Dashboard } from "@/lib/types";

/** Page title + last scan, options-CSV status and scan warnings (shared by the tabs). The "Run scan now"
 *  button is shown only where asked (Dashboard, Unusual Options); elsewhere the daily auto-scan applies. */
export function ScanBar({ title, subtitle, onScanned, showButton = false }: { title: string; subtitle?: ReactNode; onScanned?: () => void; showButton?: boolean }) {
  const { data, error, reload } = useApi<Dashboard>("/dashboard");
  const [msg, setMsg] = useState<string | null>(null);
  async function scan() {
    setMsg("Scanning… this can take a few minutes.");
    try {
      const r = await api<{ queued: boolean; status?: string; new?: string[] }>("/scan", { method: "POST" });
      setMsg(r.queued ? "Scan queued — results appear when it finishes." : `Scan finished (${r.status}).${r.new?.length ? ` New: ${r.new.join(", ")}` : ""}`);
      reload();
      onScanned?.();
    } catch (e) {
      setMsg((e as Error).message);
    }
  }
  const run = data?.latest_run;
  const o = data?.options_source;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">{title}</h1>
          <p className="text-sm text-muted">
            {subtitle ? <>{subtitle} · </> : null}Market: <span className="text-ink">{data?.market_phase ?? "…"}</span>
            {run && <> · last scan {run.session_date} ({run.kind}) {ago(run.finished_at)} <Badge tone={run.status === "ok" ? "bull" : "warn"}>{run.status}</Badge></>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {msg && <span className="text-xs text-muted">{msg}</span>}
          {showButton && <Button onClick={scan}>Run scan now</Button>}
        </div>
      </div>
      <ErrorBox error={error} />
      {o?.type === "csv" && !o.latest && (
        <div className="rounded-lg border border-warn/40 bg-warn/10 p-3 text-sm text-warn">
          No unusual-options CSV imported yet — upload one on the <Link className="underline" href="/options">Unusual Options</Link> tab.
        </div>
      )}
      {o?.type === "csv" && o.latest && !o.current && (
        <div className="rounded-lg border border-warn/40 bg-warn/10 p-3 text-sm text-warn">
          The options CSV is from {o.latest.session_date}, older than the last completed session ({o.last_completed_session}); scans treat it as missing.{" "}
          <Link className="underline" href="/options">Import a current file</Link>.
        </div>
      )}
      {run && run.relaxed_criteria.length > 0 && (
        <div className="rounded-lg border border-warn/40 bg-warn/10 p-3 text-sm text-warn">
          These criteria were relaxed (looser than the specification) for the last scan: <b>{run.relaxed_criteria.join(", ")}</b>.
        </div>
      )}
      {run && run.errors.length > 0 && (
        <details className="rounded-lg border border-warn/40 bg-warn/10 p-3 text-sm text-warn">
          <summary>{run.errors.length} data issue(s) in the last scan — affected criteria were marked missing, never passed.</summary>
          <ul className="mt-2 list-disc pl-5 text-xs">{run.errors.slice(0, 20).map((e, i) => <li key={i}>{e}</li>)}</ul>
        </details>
      )}
      <div className="flex justify-end"><ShariahLegend /></div>
    </div>
  );
}
