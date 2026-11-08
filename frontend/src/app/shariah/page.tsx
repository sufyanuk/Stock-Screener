"use client";
import { Fragment, useEffect, useRef, useState } from "react";
import { Badge, Button, Card, Empty, ErrorBox, Field, ShariahLegend, Ticker } from "@/components/ui";
import { api, useApi } from "@/lib/api";
import { compact, nf } from "@/lib/format";

type Status = "HALAL" | "NON_COMPLIANT" | "DOUBTFUL";
type Test = { key: string; label: string; numerator: string; denominator: string; op: "lt" | "le"; limit: number };
type Method = { key: string; label: string; source: string | null; configured: boolean; business: string[];
  business_tolerance_pct: number | null; tests: Test[]; builtin: boolean };
type Ratio = { key: string; label: string; value: number | null; limit: number; op: string; status: string };
type Row = { symbol: string; name: string | null; sic: string | null; sic_description: string | null; period_end: string | null;
  status: Status; status_label: string; business: string; ratios: Ratio[]; reasons: string[] };
type Load = { frames_total: number; frames_done: number; companies_total: number; companies_done: number; results: number;
  computed_at: string | null };
type Resp = { methodology: string; rows: Row[]; total: number; counts: Record<Status, number>; labels: Record<Status, string>;
  methodologies: Method[]; load: Load; primary: string; disclaimer: string };
type Rules = { methodologies: Method[]; numerators: Record<string, string>; denominators: Record<string, string>;
  ops: Record<string, string>; categories: Record<string, string> };

const TONE: Record<Status, "bull" | "bear" | "warn"> = { HALAL: "bull", NON_COMPLIANT: "bear", DOUBTFUL: "warn" };
const SHORT: Record<string, string> = { AAOIFI: "AAOIFI", FTSE: "FTSE" };

export default function ShariahPage() {
  const [method, setMethod] = useState("FTSE");
  const [status, setStatus] = useState<Status | "">("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const qs = new URLSearchParams({ methodology: method, limit: "2000" });
  if (status) qs.set("status", status);
  if (q) qs.set("q", q);
  const { data, error, reload, loading } = useApi<Resp>(`/shariah?${qs}`);
  const ld = data?.load;
  const m = data?.methodologies.find((x) => x.key === method);
  const incomplete = !!ld && (ld.frames_done < ld.frames_total || ld.companies_done < ld.companies_total);

  async function load(refresh = false) {
    setErr(null);
    setBusy("Loading SEC financial statements for all US stocks…");
    try {
      for (let i = 0; i < 40; i++) {
        const r = await api<Load & { complete: boolean; processed: number; errors: string[] }>("/shariah/load", {
          method: "POST", body: JSON.stringify({ max_seconds: 200, refresh: refresh && i === 0 }) });
        setBusy(`SEC data: ${r.frames_done} of ${r.frames_total} statement tables · industry codes for ${r.companies_done.toLocaleString()} of ${r.companies_total.toLocaleString()} companies…`);
        if (r.errors.length) setErr(r.errors[0]);
        if (r.complete || r.processed === 0) break;
      }
    } catch (e) {
      setErr((e as Error).message);
    }
    setBusy(null);
    reload();
  }

  async function setPrimary(k: string) {
    try {
      await api("/shariah/primary", { method: "PUT", body: JSON.stringify({ methodology: k }) });
      reload();
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  // Load automatically the first time (and continue an unfinished load) once per page visit.
  const auto = useRef(false);
  useEffect(() => {
    if (!ld || auto.current) return;
    if (incomplete || !ld.results) {
      auto.current = true;
      // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time automatic load when SEC data is missing
      load();
    }
  }, [ld]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Shariah screening</h1>
          <p className="max-w-4xl text-sm text-muted">
            Internal engine using actual figures from SEC filings (XBRL). Each methodology is applied separately with its own
            business exclusions and financial tests. Missing figures are never treated as compliant — the stock is marked
            DOUBTFUL / INSUFFICIENT DATA. US GAAP and IFRS (foreign) filers are covered; preferred shares, warrants, units and
            rights are not screened separately (see the company&apos;s common share).
            {ld?.computed_at && <> Last computed {ld.computed_at.slice(0, 10)} for {ld.results.toLocaleString()} stocks.</>}
          </p>
        </div>
        <ShariahLegend />
      </div>

      <Card>
        <div className="flex flex-wrap items-center gap-2">
          {(data?.methodologies ?? []).map((x) => (
            <button key={x.key} onClick={() => { setMethod(x.key); setOpen(null); }}
              className={`rounded-md px-3 py-1.5 text-sm ${method === x.key ? "bg-accent text-bg" : "border border-line text-muted hover:text-ink"}`}>
              {SHORT[x.key] ?? x.key}{!x.configured && <span className="ml-1 text-xs opacity-80">(rules not entered)</span>}
            </button>
          ))}
        </div>
        {m && (
          <p className="mt-2 text-xs text-muted">
            {m.configured ? <>Rules source: {m.source}.</> : <span className="text-warn">No thresholds have been entered for this methodology, so every stock that passes the business screen is DOUBTFUL. Enter them below from the official document.</span>}
            {data && <> Ticker colours across the app use <b>{SHORT[data.primary] ?? data.primary}</b>.
              {data.primary !== method && <button className="ml-1 text-accent underline" onClick={() => setPrimary(method)}>Use {SHORT[method]} instead</button>}</>}
          </p>
        )}
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <div className="flex flex-wrap gap-1">
            {(["", "HALAL", "NON_COMPLIANT", "DOUBTFUL"] as const).map((s) => (
              <button key={s || "all"} onClick={() => setStatus(s)}
                className={`rounded-md px-3 py-1.5 text-sm ${status === s ? "bg-panel-2 text-ink ring-1 ring-accent" : "border border-line text-muted hover:text-ink"}`}>
                {s ? data?.labels[s] ?? s : "All"}
                {data && <span className="num ml-1 text-xs text-muted">{s ? data.counts[s] : Object.values(data.counts).reduce((a, b) => a + b, 0)}</span>}
              </button>
            ))}
          </div>
          <Field label="Ticker starts with"><input value={q} onChange={(e) => setQ(e.target.value.toUpperCase())} placeholder="e.g. AA" /></Field>
          <Button variant="ghost" onClick={() => load(true)} disabled={!!busy}>{busy ? "Loading…" : "Refresh SEC data"}</Button>
        </div>
        <div className="mt-3 space-y-2">
          <ErrorBox error={error} />
          {err && <div className="text-sm text-bear">{err}</div>}
          {busy && <div className="text-sm text-accent">{busy}</div>}
        </div>
      </Card>

      <Card title={data ? `${m?.label ?? method} — ${data.total.toLocaleString()} stocks` : "Results"}>
        {data?.rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-muted">
                  <th className="py-2 pr-3">Ticker</th><th className="pr-3">Company</th><th className="pr-3">Industry (SIC)</th>
                  <th className="pr-3">Result</th>
                  {(m?.tests ?? []).map((t) => <th key={t.key} className="num pr-3" title={t.label}>{t.label}<div className="normal-case">limit {t.op === "lt" ? "<" : "≤"} {t.limit}%</div></th>)}
                  <th className="pr-3">Why</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.slice(0, 1000).map((r) => (
                  <Fragment key={r.symbol}>
                    <tr className="cursor-pointer border-t border-line hover:bg-panel-2" onClick={() => setOpen(open === r.symbol ? null : r.symbol)}>
                      <td className="py-1.5 pr-3"><Ticker symbol={r.symbol} link={false} /></td>
                      <td className="max-w-[16rem] truncate pr-3" title={r.name ?? ""}>{r.name ?? "—"}</td>
                      <td className="max-w-[14rem] truncate pr-3 text-muted" title={r.sic_description ?? ""}>{r.sic ? `${r.sic} ${r.sic_description ?? ""}` : "—"}</td>
                      <td className="pr-3"><Badge tone={TONE[r.status]}>{r.status_label}</Badge></td>
                      {(m?.tests ?? []).map((t) => {
                        const x = r.ratios.find((y) => y.key === t.key);
                        return <td key={t.key} className={`num pr-3 ${x?.status === "fail" ? "text-bear" : x?.status === "pass" ? "text-bull" : "text-muted"}`}>
                          {x?.value == null ? "missing" : `${nf(x.value, 2)}%`}</td>;
                      })}
                      <td className="max-w-[28rem] truncate pr-3 text-xs text-muted" title={r.reasons.join("\n")}>{r.reasons[0] ?? "Passes every test"}</td>
                    </tr>
                    {open === r.symbol && <tr><td colSpan={5 + (m?.tests.length ?? 0)} className="bg-panel-2 p-3"><div className="sticky left-0 max-w-[min(1100px,calc(100vw-3rem))]"><Detail symbol={r.symbol} /></div></td></tr>}
                  </Fragment>
                ))}
              </tbody>
            </table>
            {data.rows.length > 1000 && <p className="mt-2 text-xs text-muted">Showing the first 1,000 — filter by result or ticker to see the rest.</p>}
          </div>
        ) : <Empty>{loading ? "Loading…" : busy ? "Loading SEC data — results appear when it finishes." : "No stocks match."}</Empty>}
        <p className="mt-2 text-xs text-muted">{data?.disclaimer} Click a row for the figures, filing references and every methodology.</p>
      </Card>

      <RulesEditor onSaved={reload} />
    </div>
  );
}

function Detail({ symbol }: { symbol: string }) {
  type D = { symbol: string; name: string; cik: string | null; sic: string | null; sic_description: string | null; period_end: string | null;
    revenue_period_end: string | null; notes: string[];
    sources: Record<string, { tag: string; value: number; end: string; start?: string; accn: string | null }[]>;
    methods: Record<string, { methodology_label: string; status: Status; status_label: string; rules_source: string | null; reasons: string[];
      business: { status: string; explanation: string };
      ratios: { key: string; label: string; numerator_label: string; denominator_label: string; numerator_value: number | null;
        denominator_value: number | null; value: number | null; op: string; limit: number; status: string }[] }> };
  const { data, error } = useApi<D>(`/shariah/${symbol}`);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <div className="text-sm text-muted">Loading…</div>;
  return (
    <div className="space-y-3 text-sm">
      <div className="text-muted">
        <b className="text-ink">{data.name}</b> · CIK {data.cik ?? "—"} · SIC {data.sic ?? "—"} {data.sic_description ?? ""} · balance sheet {data.period_end ?? "—"} · annual income {data.revenue_period_end ?? "—"}
      </div>
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        {Object.entries(data.methods).map(([k, x]) => (
          <div key={k} className="rounded-lg border border-line p-2">
            <div className="flex flex-wrap items-center justify-between gap-2"><b>{x.methodology_label}</b><Badge tone={TONE[x.status]}>{x.status_label}</Badge></div>
            <div className="mt-1 text-xs text-muted">Business screen ({x.business.status}): {x.business.explanation}</div>
            {x.ratios.length ? (
              <table className="mt-2 w-full text-xs">
                <tbody>
                  {x.ratios.map((t) => (
                    <tr key={t.key} className="border-t border-line">
                      <td className="py-1 pr-2">{t.label}</td>
                      <td className="num pr-2 text-muted">{t.numerator_value == null ? "missing" : `$${compact(t.numerator_value)}`} / {t.denominator_value == null ? "missing" : `$${compact(t.denominator_value)}`}</td>
                      <td className={`num pr-2 ${t.status === "fail" ? "text-bear" : t.status === "pass" ? "text-bull" : "text-warn"}`}>{t.value == null ? "—" : `${nf(t.value, 2)}%`}</td>
                      <td className="num text-muted">{t.op === "lt" ? "<" : "≤"} {t.limit}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div className="mt-1 text-xs text-warn">No financial thresholds entered for this methodology.</div>}
            {!!x.reasons.length && <ul className="mt-1 list-disc pl-4 text-xs text-muted">{x.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
          </div>
        ))}
      </div>
      <div className="text-xs text-muted">
        <b className="text-ink">SEC figures used</b> (XBRL tag, value, period, accession):
        <ul className="mt-1 list-disc pl-4">
          {Object.entries(data.sources).map(([k, v]) => (
            <li key={k}>{k.replace(/_/g, " ")}: {v.map((f) => `${f.tag} = ${compact(f.value)} (${f.start ? f.start + " → " : ""}${f.end}${f.accn ? ", " + f.accn : ""})`).join(" + ")}</li>
          ))}
        </ul>
        {data.notes.map((n, i) => <div key={i}>{n}</div>)}
      </div>
    </div>
  );
}

function RulesEditor({ onSaved }: { onSaved: () => void }) {
  const { data, reload } = useApi<Rules>("/shariah/rules");
  const [key, setKey] = useState<string | null>(null);
  const [draft, setDraft] = useState<Method | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  if (!data) return null;

  function edit(k: string) {
    const m = data!.methodologies.find((x) => x.key === k)!;
    setKey(k);
    setDraft(JSON.parse(JSON.stringify(m)));
    setMsg(null);
  }
  async function save() {
    if (!draft || !key) return;
    setMsg(null);
    try {
      await api(`/shariah/rules/${key}`, { method: "PUT", body: JSON.stringify({ source: draft.source ?? "", business: draft.business,
        business_tolerance_pct: draft.business_tolerance_pct, tests: draft.tests }) });
      setMsg("Saved — results recomputed.");
      reload();
      onSaved();
    } catch (e) {
      setMsg((e as Error).message);
    }
  }
  const setTest = (i: number, patch: Partial<Test>) => setDraft((d) => d && { ...d, tests: d.tests.map((t, j) => (j === i ? { ...t, ...patch } : t)) });

  return (
    <Card title="Methodology rules">
      <p className="mb-2 text-xs text-muted">
        Rules are used only as entered here, each methodology on its own. FTSE comes from the FTSE Yasaar Global Equity Shariah
        Index Series Ground Rules. Enter AAOIFI thresholds from the current official AAOIFI Shari&apos;ah Standard No. 21 — until then
        AAOIFI classifies nothing as HALAL.
      </p>
      <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
        {data.methodologies.map((m) => (
          <div key={m.key} className="rounded-lg border border-line p-2 text-sm">
            <div className="flex items-center justify-between gap-2">
              <b>{m.label}</b>
              <Button variant="ghost" onClick={() => edit(m.key)}>Edit</Button>
            </div>
            <div className="text-xs text-muted">{m.source ?? "No source entered"}</div>
            <ul className="mt-1 text-xs">
              {m.tests.length ? m.tests.map((t) => <li key={t.key}>{t.label}: {data.numerators[t.numerator]} / {data.denominators[t.denominator]} {data.ops[t.op]} {t.limit}%</li>)
                : <li className="text-warn">No financial tests entered</li>}
            </ul>
            <div className="mt-1 text-xs text-muted">Business exclusions: {m.business.map((c) => data.categories[c]).join(", ")}
              {m.business_tolerance_pct != null && <> · revenue tolerance {m.business_tolerance_pct}%</>}</div>
          </div>
        ))}
      </div>
      {draft && key && (
        <div className="mt-3 space-y-2 rounded-lg border border-accent/40 p-3 text-sm">
          <b>Edit {draft.label}</b>
          <Field label="Official source (document name and version)"><input value={draft.source ?? ""} onChange={(e) => setDraft({ ...draft, source: e.target.value })} placeholder="e.g. AAOIFI Shari’ah Standard No. 21, edition" /></Field>
          <div className="flex flex-wrap gap-3 text-xs">
            {Object.entries(data.categories).map(([c, label]) => (
              <label key={c} className="flex items-center gap-1">
                <input type="checkbox" checked={draft.business.includes(c)}
                  onChange={(e) => setDraft({ ...draft, business: e.target.checked ? [...draft.business, c] : draft.business.filter((x) => x !== c) })} />{label}
              </label>
            ))}
          </div>
          <Field label="Revenue tolerance for excluded activities (%), blank = none">
            <input value={draft.business_tolerance_pct ?? ""} onChange={(e) => setDraft({ ...draft, business_tolerance_pct: e.target.value === "" ? null : Number(e.target.value) })} />
          </Field>
          {draft.tests.map((t, i) => (
            <div key={i} className="grid grid-cols-2 gap-2 md:grid-cols-6">
              <Field label="Label"><input value={t.label} onChange={(e) => setTest(i, { label: e.target.value })} /></Field>
              <Field label="Numerator"><select value={t.numerator} onChange={(e) => setTest(i, { numerator: e.target.value })}>
                {Object.entries(data.numerators).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
              <Field label="Denominator"><select value={t.denominator} onChange={(e) => setTest(i, { denominator: e.target.value })}>
                {Object.entries(data.denominators).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></Field>
              <Field label="Comparison"><select value={t.op} onChange={(e) => setTest(i, { op: e.target.value as Test["op"] })}>
                <option value="lt">less than (&lt;)</option><option value="le">not more than (≤)</option></select></Field>
              <Field label="Threshold (%)"><input value={t.limit} onChange={(e) => setTest(i, { limit: Number(e.target.value.replace(/[^0-9.]/g, "")) })} /></Field>
              <div className="flex items-end"><Button variant="danger" onClick={() => setDraft({ ...draft, tests: draft.tests.filter((_, j) => j !== i) })}>Remove</Button></div>
            </div>
          ))}
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" onClick={() => setDraft({ ...draft, tests: [...draft.tests, { key: `t${Date.now() % 100000}`, label: "", numerator: "debt", denominator: "total_assets", op: "lt", limit: 0 }] })}>Add test</Button>
            <Button onClick={save}>Save rules</Button>
            <Button variant="ghost" onClick={() => { setDraft(null); setKey(null); }}>Cancel</Button>
          </div>
          {msg && <div className="text-xs text-muted">{msg}</div>}
        </div>
      )}
    </Card>
  );
}
