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

export async function readOhlcv(files: File[], onProgress: (msg: string) => void): Promise<{ data: MonthEnd; stats: ParseStats }> {
  const data: MonthEnd = new Map();
  const st: ParseStats = { files: files.length, lines: 0, used: 0, skipped: 0, symbols: 0, months: 0, firstDate: null,
    lastDate: null, closeColumn: "", symbolSource: "", sample: [] };
  let bytesDone = 0;
  const total = files.reduce((a, f) => a + f.size, 0);

  for (const file of files) {
    const reader = file.stream().pipeThrough(new TextDecoderStream()).getReader();
    let rest = "", header: string[] | null = null, sep = ",";
    let iSym = -1, iDate = -1, iClose = -1, fileSym = "";

    const handle = (line: string) => {
      if (!line.trim()) return;
      if (!header) {
        sep = [",", ";", "\t"].sort((a, b) => line.split(b).length - line.split(a).length)[0];
        header = splitLine(line, sep).map(norm);
        iSym = pick(header, SYMBOL_KEYS);
        iDate = pick(header, DATE_KEYS);
        const adj = pick(header, ADJ_KEYS);
        iClose = adj >= 0 ? adj : pick(header, CLOSE_KEYS);
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
  return { data, stats: st };
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
