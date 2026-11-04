"use client";
import { CandlestickSeries, ColorType, createChart, HistogramSeries, LineSeries, LineStyle, type Time } from "lightweight-charts";
import { useEffect, useRef } from "react";

export type Bar = { time: string; open: number; high: number; low: number; close: number; volume: number };
export type Level = { price: number; label: string; color: string; dashed?: boolean };

const base = {
  layout: { background: { type: ColorType.Solid, color: "#121821" }, textColor: "#8b98a9", fontSize: 11 },
  grid: { vertLines: { color: "#1b2430" }, horzLines: { color: "#1b2430" } },
  rightPriceScale: { borderColor: "#243041" },
  timeScale: { borderColor: "#243041" },
  autoSize: true,
};

function smaSeries(bars: Bar[], n: number) {
  const out: { time: Time; value: number }[] = [];
  let sum = 0;
  bars.forEach((b, i) => {
    sum += b.close;
    if (i >= n) sum -= bars[i - n].close;
    if (i >= n - 1) out.push({ time: b.time as Time, value: sum / n });
  });
  return out;
}

export function PriceChart({ bars, levels, height = 420 }: { bars: Bar[]; levels: Level[]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !bars.length) return;
    const chart = createChart(ref.current, base);
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e", downColor: "#ef4444", borderVisible: false, wickUpColor: "#22c55e", wickDownColor: "#ef4444",
    });
    candles.setData(bars.map((b) => ({ time: b.time as Time, open: b.open, high: b.high, low: b.low, close: b.close })));
    const vol = chart.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, color: "#38bdf855" });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(bars.map((b) => ({ time: b.time as Time, value: b.volume, color: b.close >= b.open ? "#22c55e44" : "#ef444444" })));
    const mas: [number, string][] = [[20, "#a78bfa"], [50, "#38bdf8"], [100, "#f59e0b"], [200, "#e879f9"]];
    for (const [n, color] of mas) {
      if (bars.length < n) continue;
      const s = chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: `SMA${n}` });
      s.setData(smaSeries(bars, n));
    }
    for (const l of levels) {
      candles.createPriceLine({ price: l.price, color: l.color, lineWidth: 1, lineStyle: l.dashed ? LineStyle.Dashed : LineStyle.Solid, axisLabelVisible: true, title: l.label });
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, levels]);
  return <div ref={ref} style={{ height }} className="w-full overflow-hidden rounded-lg" />;
}

export function LineChart({ points, color = "#38bdf8", height = 240, bands }: {
  points: { time: string | number; value: number }[]; color?: string; height?: number;
  bands?: { time: number; p5: number; p95: number }[];
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !points.length) return;
    const indexAxis = typeof points[0].time === "number"; // trade number, not a timestamp
    const label = (t: Time) => (indexAxis ? `#${t}` : String(t));
    const chart = createChart(ref.current, {
      ...base,
      timeScale: { ...base.timeScale, timeVisible: false, ...(indexAxis ? { tickMarkFormatter: label } : {}) },
      ...(indexAxis ? { localization: { timeFormatter: label } } : {}),
    });
    if (bands) {
      const lo = chart.addSeries(LineSeries, { color: "#ef444488", lineWidth: 1, priceLineVisible: false, title: "p5" });
      const hi = chart.addSeries(LineSeries, { color: "#22c55e88", lineWidth: 1, priceLineVisible: false, title: "p95" });
      lo.setData(bands.map((b) => ({ time: b.time as Time, value: b.p5 })));
      hi.setData(bands.map((b) => ({ time: b.time as Time, value: b.p95 })));
    }
    const s = chart.addSeries(LineSeries, { color, lineWidth: 2 });
    s.setData(points.map((p) => ({ time: p.time as Time, value: p.value })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [points, color, bands]);
  return <div ref={ref} style={{ height }} className="w-full overflow-hidden rounded-lg" />;
}
