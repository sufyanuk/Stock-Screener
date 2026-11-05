"use client";
import { Empty, Info, Ticker, useSort } from "@/components/ui";
import { compact, nf, pct, tone, usd } from "@/lib/format";
import type { Rec } from "@/lib/types";

function rr(r: Rec) {
  if (!r.entry || !r.stop || !r.target1 || r.entry <= r.stop) return null;
  return (r.target1 - r.entry) / (r.entry - r.stop);
}

export function Top5({ rows }: { rows: Rec[] }) {
  const flat = rows.map((r) => {
    const o = r.criteria.options?.metrics ?? {};
    const i = r.criteria.insider?.metrics ?? {};
    const a = r.criteria.analyst?.metrics ?? {};
    const t = r.criteria.technical?.metrics ?? {};
    const f = r.criteria.float?.metrics ?? {};
    return {
      ...r, vol_chg: o.volume_change_pct, ivr: o.iv_rank, pcr: o.put_call_ratio, ins_shares: i.aggregate_shares,
      ins_pct: i.aggregate_pct, consensus: a.consensus, upside: a.upside_pct, sup: t.support_distance_pct,
      float_ratio: f.float_ratio_pct, rr: rr(r), shariah: r.extra?.shariah?.status,
    };
  });
  const { sorted, th } = useSort(flat, "rank" as keyof (typeof flat)[number], false);
  if (!rows.length) return <Empty>No stocks met every requirement in the latest scan. Nothing has been substituted — see the near-qualified list below.</Empty>;
  return (
    <div className="overflow-x-auto">
      <table className="text-sm">
        <thead>
          <tr>
            {th("rank", "#")}{th("ticker", "Ticker")}{th("company", "Company")}{th("price_at_rec", "Price")}{th("day_change_pct", "Day")}
            {th("vol_chg", <>Opt vol Δ<Info>Current-session options volume vs. the mean of the 20 sessions ending 21 sessions earlier. (current ÷ baseline − 1) × 100.</Info></>)}
            {th("ivr", <>IV rank<Info>(IV30 now − 52-week low) ÷ (52-week high − low) × 100, using 252 sessions of 30-day ATM implied volatility.</Info></>)}
            {th("pcr", <>P/C<Info>Put volume ÷ call volume for the evaluation session (volume, not open interest).</Info></>)}
            {th("ins_shares", "Insider sh.")}
            {th("ins_pct", <>Insider %<Info>Unique open-market purchases (Form 4 code P) in the look-back window ÷ shares outstanding. Joint filings of one purchase are counted once.</Info></>)}
            {th("consensus", "Analysts")}{th("upside", "Upside")}
            {th("sup", <>To support<Info>(Price − top of nearest validated support zone) ÷ zone top × 100. A zone needs ≥ 2 swing-low touches.</Info></>)}
            {th("float_ratio", <>Float<Info>Public float ÷ shares outstanding × 100.</Info></>)}
            {th("score", <>Score<Info>Weighted sum of six 0–100 factor scores (options 25, insider 20, analyst 15, technical 20, float 10, liquidity 10).</Info></>)}
            {th("entry", "Entry")}{th("stop", "Stop")}{th("target1", "T1")}{th("target2", "T2")}{th("rr", "R/R")}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.id}>
              <td className="num">{r.rank}</td>
              <td><Ticker symbol={r.ticker} status={r.shariah} /></td>
              <td className="max-w-48 truncate text-muted">{r.company}</td>
              <td className="num">{usd(r.price_at_rec)}</td>
              <td className={`num ${tone(r.day_change_pct)}`}>{pct(r.day_change_pct, 2, true)}</td>
              <td className="num text-bull">{pct(r.vol_chg, 0, true)}</td>
              <td className="num">{nf(r.ivr, 1)}</td>
              <td className="num">{nf(r.pcr, 3)}</td>
              <td className="num">{compact(r.ins_shares)}</td>
              <td className="num">{pct(r.ins_pct, 2)}</td>
              <td>{r.consensus ?? "—"}</td>
              <td className={`num ${tone(r.upside)}`}>{pct(r.upside, 1, true)}</td>
              <td className="num">{pct(r.sup, 2)}</td>
              <td className="num">{pct(r.float_ratio, 1)}</td>
              <td className="num font-semibold">{nf(r.score, 1)}</td>
              <td className="num">{usd(r.entry)}</td>
              <td className="num text-bear">{usd(r.stop)}</td>
              <td className="num text-bull">{usd(r.target1)}</td>
              <td className="num text-bull">{usd(r.target2)}</td>
              <td className="num">{r.rr ? `${nf(r.rr, 2)}R` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

