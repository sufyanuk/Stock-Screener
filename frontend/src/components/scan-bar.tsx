"use client";
import Link from "next/link";
import { ReactNode } from "react";
import { Badge, ErrorBox, ShariahLegend } from "@/components/ui";
import { useApi } from "@/lib/api";
import { ago } from "@/lib/format";
import type { Dashboard } from "@/lib/types";

/** Page title + last scan and options-CSV status (shared by the tabs). Scans run automatically: once on the
 *  first open of each day and on the weekday schedule after the US close. */
export function ScanBar({ title, subtitle }: { title: string; subtitle?: ReactNode; onScanned?: () => void }) {
  const { data, error } = useApi<Dashboard>("/dashboard");
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
      <div className="flex justify-end"><ShariahLegend /></div>
    </div>
  );
}
