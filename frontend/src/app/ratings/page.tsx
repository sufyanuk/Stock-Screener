"use client";
import { useEffect, useRef, useState } from "react";
import { Badge, Card, Empty, ErrorBox, ShariahLegend, Ticker, useSort } from "@/components/ui";
import { api, JOBS_ON_GITHUB, useApi } from "@/lib/api";
import { nf, usd } from "@/lib/format";

type Sub = "analysts" | "sentisense";

export default function RatingsPage() {
  const [sub, setSub] = useState<Sub>("analysts");
  useEffect(() => {
    try {
      const v = localStorage.getItem("ratings-sub");
      // eslint-disable-next-line react-hooks/set-state-in-effect -- restore the saved sub-tab once
      if (v === "analysts" || v === "sentisense") setSub(v);
    } catch { /* storage unavailable */ }
  }, []);
  function pick(v: Sub) {
    setSub(v);
    try { localStorage.setItem("ratings-sub", v); } catch { /* storage unavailable */ }
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Ratings</h1>
          <p className="text-sm text-muted">For the stocks the app follows: your watchlist, the latest unusual-options CSV, insider purchases (90 days) and earnings this week.</p>
        </div>
        <ShariahLegend />
      </div>
      <div className="flex gap-1">
        {([["analysts", "Analyst ratings — Strong Buy"], ["sentisense", "SentiSense rating"]] as const).map(([k, label]) => (
          <button key={k} onClick={() => pick(k)}
            className={`rounded-md px-3 py-1.5 text-sm ${sub === k ? "bg-accent text-bg" : "border border-line text-muted hover:text-ink"}`}>{label}</button>
        ))}
      </div>
      {sub === "analysts" ? <AnalystRatings /> : <SentiSenseRatings />}
    </div>
  );
}

type ARow = {
  symbol: string; name: string | null; strong_buy: number; buy: number | null; hold: number | null; sell: number | null; strong_sell: number | null;
  total: number; strong_buy_pct: number | null; buy_pct: number | null; provider: string; retrieved_at: string; shariah_status: string | null;
  price: number | null; price_as_of?: string | null;
};

function AnalystRatings() {
  const [minSb, setMinSb] = useState(1);
  const [q, setQ] = useState("");
  const { data, error, reload, loading } = useApi<{ rows: ARow[]; universe: number; missing: number }>(`/ratings/analysts?min_strong_buy=${minSb}`);
  const [busy, setBusy] = useState<string | null>(null);
  const auto = useRef(false);

  async function load() {
    setBusy("Loading analyst ratings…");
    try {
      for (let i = 0; i < 25; i++) {
        const r = await api<{ fetched: number; remaining: number; errors: string[] }>("/ratings/analysts/load?limit=100", { method: "POST" });
        setBusy(`Loaded ${r.fetched}; ${r.remaining} remaining…`);
        reload();
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
  useEffect(() => {
    if (data && data.missing > 0 && !auto.current && !JOBS_ON_GITHUB) {
      auto.current = true;
      load();
    }
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  const rows = (data?.rows ?? []).filter((r) => !q || r.symbol.includes(q.toUpperCase()) || (r.name ?? "").toLowerCase().includes(q.toLowerCase()))
    .map((r) => ({ ...r, updated: r.retrieved_at.slice(0, 10) }));
  const { sorted, th } = useSort(rows, "strong_buy" as keyof (typeof rows)[number], true);
  return (
    <Card title={`${rows.length} stocks with ${minSb === 1 ? "at least one" : `${minSb}+`} Strong Buy rating`} right={
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by ticker or name" className="w-44" />
        <label className="flex items-center gap-1.5">Min Strong Buy
          <select value={minSb} onChange={(e) => setMinSb(Number(e.target.value))}>{[1, 2, 3, 5, 10].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
        {busy && <span className="text-xs text-muted">{busy}</span>}
      </div>}>
      <ErrorBox error={error} />
      {loading && !data ? <Empty>Loading…</Empty> : sorted.length ? (
        <div className="overflow-x-auto">
          <table className="text-sm">
            <thead><tr>
              {th("symbol", "Ticker")}{th("name", "Company")}{th("price", "Price")}{th("strong_buy", "Strong Buy")}{th("buy", "Buy")}{th("hold", "Hold")}
              {th("sell", "Sell")}{th("strong_sell", "Strong Sell")}{th("total", "Total analysts")}{th("strong_buy_pct", "% Strong Buy")}
              {th("buy_pct", "% Buy + Strong Buy")}{th("provider", "Source")}{th("updated", "Updated")}
            </tr></thead>
            <tbody>{sorted.map((r) => (
              <tr key={r.symbol}>
                <td><Ticker symbol={r.symbol} status={r.shariah_status} /></td>
                <td className="max-w-56 truncate text-muted">{r.name ?? "—"}</td>
                <td className="num" title={r.price_as_of ? `Close on ${r.price_as_of}` : undefined}>{usd(r.price)}</td>
                <td className="num"><Badge tone="bull">{r.strong_buy}</Badge></td>
                <td className="num">{r.buy ?? "—"}</td><td className="num">{r.hold ?? "—"}</td>
                <td className="num">{r.sell ?? "—"}</td><td className="num">{r.strong_sell ?? "—"}</td>
                <td className="num">{r.total}</td>
                <td className="num">{nf(r.strong_buy_pct, 0)}%</td>
                <td className="num text-bull">{nf(r.buy_pct, 0)}%</td>
                <td className="text-xs text-muted">{r.provider}</td>
                <td className="text-xs text-muted">{r.updated}</td>
              </tr>))}</tbody>
          </table>
        </div>
      ) : <Empty>{data?.missing ? "Loading analyst ratings for the followed stocks…" : "No stocks with Strong Buy ratings yet — run a scan or add stocks to your watchlist."}</Empty>}
      {data && <p className="mt-2 text-xs text-muted">Latest monthly analyst recommendation counts (Finnhub; FMP / Alpha Vantage as fallback), cached for a day. {data.universe.toLocaleString()} stocks followed{data.missing ? `, ${data.missing.toLocaleString()} still loading` : ""}.</p>}
    </Card>
  );
}

type SRow = {
  symbol: string; name: string | null; rated: boolean; letter: string | null; score: number | null; percentile: number | null;
  penalty_points: number | null; analyst_favorable_pct: number | null; as_of: string | null; reason: string | null; source: string; shariah_status: string | null;
  price: number | null; price_as_of?: string | null;
};
const LETTERS = ["A", "B", "C", "D", "F"];
const LETTER_TONE: Record<string, string> = { A: "text-bull", B: "text-bull", C: "text-warn", D: "text-bear", F: "text-bear" };

function SentiSenseRatings() {
  const { data, error, reload, loading } = useApi<{ rows: SRow[]; retrieved_at: string | null; source: string | null }>("/ratings/sentisense");
  const [letters, setLetters] = useState<string[]>(["A", "B"]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const auto = useRef(false);

  async function load() {
    setBusy("Loading SentiSense ratings…");
    try {
      for (let i = 0; i < 8; i++) {
        const r = await api<{ fetched: number; remaining: number; mode: string | null; errors: string[] }>("/ratings/sentisense/load", { method: "POST" });
        reload();
        setBusy(r.mode === "per-stock" && r.remaining ? `Loaded ${r.fetched} more; ${r.remaining} remaining…` : null);
        if (r.errors.length && r.fetched === 0) { setBusy(`SentiSense: ${r.errors[0]}`); break; }
        if (r.remaining === 0 || r.fetched === 0) break;
      }
    } catch (e) {
      setBusy((e as Error).message);
    }
    reload();
  }
  useEffect(() => {
    if (!data || auto.current || JOBS_ON_GITHUB) return;
    const stale = !data.retrieved_at || Date.now() - new Date(data.retrieved_at).getTime() > 20 * 3600 * 1000;
    if (stale) {
      auto.current = true;
      // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time automatic load when data is missing
      load();
    }
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (l: string) => setLetters((x) => (x.includes(l) ? x.filter((y) => y !== l) : [...x, l]));
  const rows = (data?.rows ?? []).filter((r) => (r.rated ? letters.includes(r.letter ?? "") : letters.includes("NR")) &&
    (!q || r.symbol.includes(q.toUpperCase()) || (r.name ?? "").toLowerCase().includes(q.toLowerCase())));
  const { sorted, th } = useSort(rows, "score" as keyof SRow, true);
  return (
    <Card title={`${rows.length} stocks`} right={
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter by ticker or name" className="w-44" />
        {[...LETTERS, "NR"].map((l) => (
          <button key={l} onClick={() => toggle(l)} title={l === "NR" ? "Not rated" : `Rating ${l}`}
            className={`rounded-md px-2.5 py-1 ${letters.includes(l) ? "bg-accent text-bg" : "border border-line text-muted hover:text-ink"}`}>{l}</button>
        ))}
        {busy && <span className="text-xs text-muted">{busy}</span>}
      </div>}>
      <ErrorBox error={error} />
      {loading && !data ? <Empty>Loading…</Empty> : sorted.length ? (
        <div className="overflow-x-auto">
          <table className="text-sm">
            <thead><tr>
              {th("symbol", "Ticker")}{th("name", "Company")}{th("price", "Price")}{th("letter", "Rating")}{th("score", "Score / 100")}{th("percentile", "Rank (percentile)")}
              {th("penalty_points", "Risk points")}{th("analyst_favorable_pct", "Favourable analysts")}{th("as_of", "As of")}
            </tr></thead>
            <tbody>{sorted.map((r) => (
              <tr key={r.symbol}>
                <td><Ticker symbol={r.symbol} status={r.shariah_status} /></td>
                <td className="max-w-56 truncate text-muted">{r.name ?? "—"}</td>
                <td className="num" title={r.price_as_of ? `Close on ${r.price_as_of}` : undefined}>{usd(r.price)}</td>
                <td className={`text-lg font-bold ${LETTER_TONE[r.letter ?? ""] ?? "text-muted"}`}>{r.rated ? r.letter : <span className="text-xs font-normal" title={r.reason ?? ""}>not rated</span>}</td>
                <td className="num">{nf(r.score, 1)}</td>
                <td className="num">{r.percentile != null ? `${nf(r.percentile, 0)}th` : "—"}</td>
                <td className="num">{r.penalty_points != null ? `−${nf(r.penalty_points, 1)}` : "—"}</td>
                <td className="num">{r.analyst_favorable_pct != null ? `${nf(r.analyst_favorable_pct, 0)}%` : "—"}</td>
                <td className="text-xs text-muted">{r.as_of ?? "—"}</td>
              </tr>))}</tbody>
          </table>
        </div>
      ) : <Empty>{data?.rows.length ? "No stocks with the selected ratings." : busy ? "Loading SentiSense ratings…" : "No SentiSense ratings yet — check that SENTISENSE_API_KEY is set."}</Empty>}
      <p className="mt-2 text-xs text-muted">
        SentiSense Rating: a 0–100 score (A ≥ 90, B 70–89.9, C 30–69.9, D 10–29.9, F &lt; 10) ranking each stock across crowd sentiment, smart money,
        options, analysts, fundamentals, earnings and technicals, less risk adjustments. It is a research score, not a buy/sell recommendation.
        {data?.source && <> Source: {data.source === "screener" ? "SentiSense screener (all rated stocks)" : "per-stock ratings for the followed stocks"}{data.retrieved_at ? `, updated ${data.retrieved_at.slice(0, 16).replace("T", " ")} UTC` : ""}.</>}
      </p>
    </Card>
  );
}
