/**
 * Reads historical OHLCV CSV files in the browser and reduces them to the last close of each month
 * per stock — only that small summary is uploaded, so files of any size work.
 *
 * Accepted layouts (header row required, comma / semicolon / tab separated):
 *   - one file with many stocks: Symbol|Ticker, Date, Open, High, Low, Close, [Adj Close], Volume
 *   - one file per stock without a symbol column: the ticker is taken from the file name (AAPL.csv)
 * Dates: YYYY-MM-DD (time part ignored), YYYYMMDD, MM/DD/YYYY, or Unix timestamps.
 * If an adjusted-close column exists it is used; otherwise Close.
 */

export type MonthEnd = Map<string, Map<string, [string, number]>>; // symbol -> "YYYY-MM" -> [ISO date, close]
type Bar = [string, number, number, number, number, number]; // date, open, high, low, close, volume
export type Recent = Map<string, Bar[]>;
export type Snapshot = {
  symbol: string; n: number; d: string[]; o: number[]; h: number[]; l: number[]; c: number[]; v: number[]; ema50: (number | null)[] | null;
  low52: number; low52_date: string; low52_ago: number; high52: number; high52_date: string; avg_vol50: number | null;
};

export type ParseStats = {
  files: number; lines: number; used: number; skipped: number; symbols: number; months: number;
  firstDate: string | null; lastDate: string | null; closeColumn: string; symbolSource: string; sample: string[];
};

const norm = (h: string) => h.trim().replace(/^﻿/, "").replace(/^"|"$/g, "").toLowerCase().replace(/[^a-z]/g, "");
const SYMBOL_KEYS = ["symbol", "ticker", "sym", "tickersymbol", "code", "stock"];
const DATE_KEYS = ["date", "datetime", "timestamp", "time", "day", "tradedate", "tradingdate"];
const ADJ_KEYS = ["adjclose", "adjustedclose", "closeadj", "adjustedcloseprice", "adjclo"];
const CLOSE_KEYS = ["close", "closelast", "closeprice", "last", "price"];

function pick(cols: string[], keys: string[]) {
  for (const k of keys) {
    const i = cols.indexOf(k);
    if (i >= 0) return i;
  }
  return -1;
}

function splitLine(line: string, sep: string): string[] {
  if (!line.includes('"')) return line.split(sep);
  const out: string[] = [];
  let cur = "", q = false;
  for (const ch of line) {
    if (ch === '"') q = !q;
    else if (ch === sep && !q) { out.push(cur); cur = ""; }
    else cur += ch;
  }
  out.push(cur);
  return out;
}

export function parseDate(v: string): string | null {
  const s = v.trim().replace(/^"|"$/g, "");
  let m = /^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})/.exec(s);
  if (m) return `${m[1]}-${m[2].padStart(2, "0")}-${m[3].padStart(2, "0")}`;
  m = /^(\d{4})(\d{2})(\d{2})$/.exec(s);
  if (m) return `${m[1]}-${m[2]}-${m[3]}`;
  m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})/.exec(s); // US month/day/year
  if (m) return `${m[3]}-${m[1].padStart(2, "0")}-${m[2].padStart(2, "0")}`;
  if (/^\d{9,13}$/.test(s)) {
    const n = Number(s);
    return new Date(s.length > 10 ? n : n * 1000).toISOString().slice(0, 10);
  }
  return null;
}

const num = (v: string) => {
  const n = Number(v.trim().replace(/^"|"$/g, "").replace(/[$,\s]/g, ""));
  return Number.isFinite(n) ? n : NaN;
};

function symbolFromFile(name: string) {
  return name.replace(/\.[^.]+$/, "").split(/[_\s]/)[0].toUpperCase();
}

export async function readOhlcv(files: File[], onProgress: (msg: string) => void): Promise<{ data: MonthEnd; recent: Recent; stats: ParseStats }> {
  const data: MonthEnd = new Map();
  const recent: Recent = new Map();
  // keep ~14 months of daily bars for the 52-week-low and 50-EMA screens
  const cutoff = new Date(Date.now() - 430 * 86_400_000).toISOString().slice(0, 10);
  const st: ParseStats = { files: files.length, lines: 0, used: 0, skipped: 0, symbols: 0, months: 0, firstDate: null,
    lastDate: null, closeColumn: "", symbolSource: "", sample: [] };
  let bytesDone = 0;
  const total = files.reduce((a, f) => a + f.size, 0);

  for (const file of files) {
    const reader = file.stream().pipeThrough(new TextDecoderStream()).getReader();
    let rest = "", header: string[] | null = null, sep = ",";
    let iSym = -1, iDate = -1, iClose = -1, iO = -1, iH = -1, iL = -1, iV = -1, iRaw = -1, fileSym = "";

    const handle = (line: string) => {
      if (!line.trim()) return;
      if (!header) {
        sep = [",", ";", "\t"].sort((a, b) => line.split(b).length - line.split(a).length)[0];
        header = splitLine(line, sep).map(norm);
        iSym = pick(header, SYMBOL_KEYS);
        iDate = pick(header, DATE_KEYS);
        const adj = pick(header, ADJ_KEYS);
        iRaw = pick(header, CLOSE_KEYS);
        iClose = adj >= 0 ? adj : iRaw;
        iO = pick(header, ["open", "openprice"]); iH = pick(header, ["high", "highprice"]);
        iL = pick(header, ["low", "lowprice"]); iV = pick(header, ["volume", "vol"]);
        if (iDate < 0 || iClose < 0)
          throw new Error(`${file.name}: needs a header row with Date and Close columns (found: ${header.join(", ")})`);
        if (iSym < 0) fileSym = symbolFromFile(file.name);
        st.closeColumn = adj >= 0 ? "adjusted close" : "close";
        st.symbolSource = iSym >= 0 ? "symbol column" : "file name";
        return;
      }
      st.lines++;
      const c = splitLine(line, sep);
      const sym = iSym >= 0 ? (c[iSym] ?? "").trim().replace(/^"|"$/g, "").toUpperCase() : fileSym;
      const d = parseDate(c[iDate] ?? "");
      const px = num(c[iClose] ?? "");
      if (!sym || !d || !(px > 0)) { st.skipped++; return; }
      st.used++;
      const mk = d.slice(0, 7);
      let m = data.get(sym);
      if (!m) { m = new Map(); data.set(sym, m); }
      const prev = m.get(mk);
      if (!prev || d >= prev[0]) m.set(mk, [d, px]);
      if (d >= cutoff) {
        // scale OHLC by the adjustment factor so they line up with the adjusted close
        const raw = iRaw >= 0 && iRaw !== iClose ? num(c[iRaw] ?? "") : px;
        const f = raw > 0 ? px / raw : 1;
        const g = (i: number) => { const x = i >= 0 ? num(c[i] ?? "") : NaN; return x > 0 ? x * f : px; };
        let r = recent.get(sym);
        if (!r) { r = []; recent.set(sym, r); }
        r.push([d, g(iO), g(iH), g(iL), px, iV >= 0 ? num(c[iV] ?? "") || 0 : 0]);
      }
      if (!st.firstDate || d < st.firstDate) st.firstDate = d;
      if (!st.lastDate || d > st.lastDate) st.lastDate = d;
    };

    for (;;) {
      const { value, done } = await reader.read();
      if (value) {
        bytesDone += value.length;
        const parts = (rest + value).split(/\r?\n/);
        rest = parts.pop() ?? "";
        for (const p of parts) handle(p);
        onProgress(`Reading ${file.name}… ${Math.min(99, Math.round((bytesDone / Math.max(total, 1)) * 100))}% · ${st.used.toLocaleString()} rows`);
      }
      if (done) break;
    }
    handle(rest);
  }
  st.symbols = data.size;
  const months = new Set<string>();
  for (const m of data.values()) for (const k of m.keys()) months.add(k);
  st.months = months.size;
  st.sample = [...data.keys()].slice(0, 8);
  return { data, recent, stats: st };
}

/** Last 15 bars, 50-day EMA, 52-week low/high and 50-day average volume per stock. */
export function snapshots(recent: Recent): Snapshot[] {
  const out: Snapshot[] = [];
  for (const [symbol, raw] of recent) {
    const byDate = new Map<string, Bar>();
    for (const b of raw) byDate.set(b[0], b); // de-duplicate dates (last row wins)
    const bars = [...byDate.values()].sort((a, b) => (a[0] < b[0] ? -1 : 1));
    if (bars.length < 15) continue;
    const y = bars.slice(-252);
    let lo = 0, hi = 0;
    y.forEach((b, i) => { if (b[3] <= y[lo][3]) lo = i; if (b[2] >= y[hi][2]) hi = i; });
    const ema: (number | null)[] = new Array(bars.length).fill(null);
    if (bars.length >= 50) {
      let e = bars.slice(0, 50).reduce((a, b) => a + b[4], 0) / 50;
      ema[49] = e;
      const k = 2 / 51;
      for (let i = 50; i < bars.length; i++) { e = bars[i][4] * k + e * (1 - k); ema[i] = e; }
    }
    const last = bars.slice(-15);
    const v50 = bars.slice(-50).map((b) => b[5]).filter((v) => v > 0);
    out.push({
      symbol, n: bars.length, d: last.map((b) => b[0]), o: last.map((b) => b[1]), h: last.map((b) => b[2]), l: last.map((b) => b[3]),
      c: last.map((b) => b[4]), v: last.map((b) => b[5]), ema50: bars.length >= 60 ? ema.slice(-15) : null,
      low52: y[lo][3], low52_date: y[lo][0], low52_ago: y.length - 1 - lo, high52: y[hi][2], high52_date: y[hi][0],
      avg_vol50: v50.length >= 20 ? v50.reduce((a, b) => a + b, 0) / v50.length : null,
    });
  }
  return out;
}

/** Rows [symbol, "YYYY-MM", close] grouped so one symbol is never split across chunks. */
export function chunks(data: MonthEnd, maxRows = 40_000): (string | number)[][][] {
  const out: (string | number)[][][] = [];
  let cur: (string | number)[][] = [];
  for (const [sym, m] of data) {
    if (cur.length && cur.length + m.size > maxRows) { out.push(cur); cur = []; }
    for (const [mk, [, px]] of m) cur.push([sym, mk, px]);
  }
  if (cur.length) out.push(cur);
  return out;
}
