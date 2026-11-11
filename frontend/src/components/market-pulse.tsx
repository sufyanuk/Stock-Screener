"use client";
/** Dashboard "Market pulse": Market Mood, Macro backdrop, next Fed decision and S&P 500 breadth (stored nightly). */
import { Card } from "@/components/ui";
import { useApi } from "@/lib/api";

type Comp = { name: string; value?: number; score?: number; detail?: string; change?: number | null };
type Pulse = {
  computed_at: string | null;
  breadth?: { advancing: number; declining: number; unchanged: number; total: number; as_of: string | null; source?: string };
  mood?: { score: number | null; label: string | null; components: Comp[]; source?: string; weekly_change?: number | null };
  macro?: { score: number | null; label: string | null; components: Comp[]; missing?: string };
  fed?: { next_decision: string | null; source: string };
};

const moodTone = (l: string | null | undefined) =>
  l && /Fear|Anxiety/.test(l) ? "text-bear" : l && /Greed|Optimism/.test(l) ? "text-bull" : "text-ink";
const macroTone = (l: string | null | undefined) => (l === "Hostile" ? "text-bear" : l === "Supportive" ? "text-bull" : "text-ink");

function fedLine(iso: string | null | undefined) {
  if (!iso) return null;
  const d = new Date(iso + "T00:00:00");
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const n = Math.round((d.getTime() - today.getTime()) / 864e5);
  const label = d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
  return { label, when: n <= 0 ? "today" : n === 1 ? "tomorrow" : `in ${n} days` };
}

export function MarketPulse() {
  const { data } = useApi<Pulse>("/market-pulse");
  if (!data || !data.computed_at) return null;  // nothing stored yet (first nightly run pending)
  const b = data.breadth, m = data.mood, mc = data.macro, fed = fedLine(data.fed?.next_decision);
  const moodTip = [m?.source ? `Source: ${m.source}` : "", m?.weekly_change != null ? `Week: ${m.weekly_change > 0 ? "+" : ""}${m.weekly_change}` : "",
    ...(m?.components ?? []).map((c) => `${c.name}: ${c.detail ?? `${c.value}`}`)].filter(Boolean).join("\n");
  const macroTip = mc?.components.map((c) => `${c.name}: ${c.detail} (${(c.score ?? 0) > 0 ? "+" : ""}${c.score})`).join("\n");
  const moved = b ? b.advancing + b.declining : 0;
  return (
    <Card>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-1 text-sm">
          <div title={moodTip}><span className="text-muted">Market Mood </span>
            {m?.score != null ? <><b className={moodTone(m.label)}>{m.label}</b> <span className="num">{m.score}</span></> : <span className="text-muted">—</span>}</div>
          <div title={macroTip ?? mc?.missing}><span className="text-muted">Macro backdrop </span>
            {mc?.score != null ? <><b className={macroTone(mc.label)}>{mc.label}</b> <span className="num">{mc.score > 0 ? `+${mc.score}` : mc.score}</span></>
              : <span className="text-xs text-muted">{mc?.missing ?? "—"}</span>}</div>
          <div title={data.fed?.source ? `Next FOMC decision (${data.fed.source})` : undefined}><span className="text-muted">Fed </span>
            {fed ? <><b>{fed.label}</b> <span className="text-muted">{fed.when}</span></> : <span className="text-muted">—</span>}</div>
        </div>
        {b && b.total > 0 ? (
          <div className="self-center">
            <div className="flex items-baseline gap-4 text-sm">
              <span><b className="num text-bull">{b.advancing}</b> <span className="text-muted">advancing</span></span>
              <span><b className="num text-bear">{b.declining}</b> <span className="text-muted">declining</span></span>
            </div>
            <div className="mt-1 flex h-2.5 overflow-hidden rounded-full bg-panel-2" title={`S&P 500: ${b.advancing} up, ${b.declining} down, ${b.unchanged} unchanged`}>
              {moved > 0 && <><div className="bg-bull/70" style={{ width: `${(b.advancing / b.total) * 100}%` }} />
                <div className="bg-muted/30" style={{ width: `${(b.unchanged / b.total) * 100}%` }} />
                <div className="bg-bear/70" style={{ width: `${(b.declining / b.total) * 100}%` }} /></>}
            </div>
            <div className="mt-1 text-xs text-muted">{b.unchanged} unchanged · S&amp;P 500{b.as_of ? ` · ${b.as_of}` : ""}{b.source ? ` · ${b.source}` : ""}</div>
          </div>
        ) : <div className="self-center text-xs text-muted">S&amp;P 500 breadth not available yet.</div>}
      </div>
      <p className="mt-2 text-[11px] text-muted">Updated by the nightly run ({new Date(data.computed_at).toLocaleString()}). Market Mood {m?.source === "SentiSense" ? "and breadth from SentiSense" : "is the app\u2019s own breadth gauge"} (0 = extreme fear, 100 = extreme greed); macro from FRED; Fed from federalreserve.gov. Hover the lines for details.</p>
    </Card>
  );
}
