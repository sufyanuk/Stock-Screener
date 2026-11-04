"""Parse an unusual-options-volume CSV (Barchart "Unusual Options Volume" export, or the same
columns saved from a spreadsheet).

Expected columns (case/spacing-insensitive; aliases accepted):
    Symbol, Name, Latest|Last, Change, %Change, IV Rank, Options Vol, 1M Vol %Chg,
    Put Vol, Call Vol, P/C Vol, Date|Time

Percent columns (%Change, IV Rank, 1M Vol %Chg) may be text with a percent sign
("+1,639.17%", "3.27%") or plain numbers. Plain numbers are interpreted as **fractions**
(0.2 = 20%, 16.39 = 1,639%) — the convention of a spreadsheet that stored the export as
percentages — unless ``percent_numbers="percent"`` is passed. The interpretation used is
reported back so it can be shown to the user.

"1M Vol %Chg" is the source's own month-over-month options-volume change; the baseline volume
is derived from it as current ÷ (1 + change).
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime

ALIASES = {
    "symbol": ("symbol", "ticker", "underlying"),
    "name": ("name", "company", "companyname"),
    "price": ("latest", "last", "price", "lastprice", "stockprice"),
    "change": ("change", "chg", "netchange"),
    "pct_change": ("%change", "%chg", "pctchange", "percentchange", "changepct"),
    "iv_rank": ("ivrank", "ivr", "impliedvolatilityrank"),
    "options_volume": ("optionsvol", "optionsvolume", "totaloptionsvolume", "optvol"),
    "vol_change_1m": ("1mvol%chg", "1mvol%change", "1mvolchg", "1mvolumechange", "volchange1m",
                      "1mvol%chg.", "1monthvol%chg"),
    "put_volume": ("putvol", "putvolume", "puts"),
    "call_volume": ("callvol", "callvolume", "calls"),
    "pc_ratio": ("p/cvol", "p/cratio", "putcallratio", "p/c", "pcratio", "put/callvol", "put/callratio"),
    "date": ("date", "time", "tradetime", "asof", "tradedate"),
}
PERCENT_FIELDS = ("pct_change", "iv_rank", "vol_change_1m")
REQUIRED = ("symbol", "options_volume", "vol_change_1m", "iv_rank", "put_volume", "call_volume")
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", "%d-%b-%Y", "%Y-%m-%d %H:%M:%S",
                "%m/%d/%Y %H:%M", "%m/%d/%y %H:%M")
FOOTER_DATE = re.compile(r"as of\s+(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})", re.I)
TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


class ImportError_(ValueError):
    pass


@dataclass
class ImportedRow:
    symbol: str
    name: str | None
    price: float | None
    change: float | None
    pct_change: float | None  # percent, e.g. 0.54 means 0.54%
    iv_rank: float | None  # percent 0..100
    options_volume: int | None
    vol_change_1m_pct: float | None  # percent, e.g. 1639.17
    put_volume: int | None
    call_volume: int | None
    pc_ratio: float | None
    session: date | None


@dataclass
class ParseResult:
    rows: list[ImportedRow]
    session_date: date | None
    percent_interpretation: str
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    columns: dict[str, str] = field(default_factory=dict)

    def summary(self) -> dict:
        return {"rows": len(self.rows), "session_date": self.session_date.isoformat() if self.session_date else None,
                "percent_interpretation": self.percent_interpretation, "skipped": self.skipped[:50],
                "skipped_count": len(self.skipped), "warnings": self.warnings, "columns": self.columns}


def _norm(h: str) -> str:
    return re.sub(r"[\s_]+", "", (h or "").strip().lower().replace("﻿", ""))


def _num(raw: str | None) -> tuple[float | None, bool]:
    """Returns (value, had_percent_sign)."""
    if raw is None:
        return None, False
    s = str(raw).strip()
    if s in ("", "-", "--", "N/A", "n/a", "NA", "unch", "UNCH"):
        return (0.0, False) if s.lower() == "unch" else (None, False)
    pct = s.endswith("%")
    s = s.rstrip("%").replace(",", "").replace("+", "").strip()
    try:
        return float(s), pct
    except ValueError:
        return None, pct


def _date(raw: str | None) -> date | None:
    if not raw:
        return None
    s = str(raw).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse(text: str, percent_numbers: str = "auto", session_override: date | None = None) -> ParseResult:
    if len(text) > 10_000_000:
        raise ImportError_("file too large (max 10 MB)")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    footer_date = None
    body = []
    for ln in lines:
        if "downloaded from" in ln.lower() or ln.lower().startswith('"downloaded'):
            m = FOOTER_DATE.search(ln)
            if m:
                mo, d, y = (int(x) for x in m.groups())
                footer_date = date(y + 2000 if y < 100 else y, mo, d)
            continue
        body.append(ln)
    if not body:
        raise ImportError_("the file is empty")
    reader = csv.reader(io.StringIO("\n".join(body)))
    header = next(reader)
    norm = [_norm(h) for h in header]
    col: dict[str, int] = {}
    for key, names in ALIASES.items():
        for i, h in enumerate(norm):
            if h in names and key not in col:
                col[key] = i
    missing = [k for k in REQUIRED if k not in col]
    if missing:
        raise ImportError_(f"missing required column(s): {', '.join(missing)}. Found: {', '.join(header)}")

    raw_rows = list(reader)
    had_pct = False
    parsed: list[dict] = []
    skipped: list[str] = []
    for n, r in enumerate(raw_rows, start=2):
        get = lambda k: r[col[k]] if k in col and col[k] < len(r) else None  # noqa: E731
        sym = (get("symbol") or "").strip().upper()
        if not TICKER.match(sym):
            skipped.append(f"line {n}: invalid symbol {sym!r}")
            continue
        vals: dict = {"symbol": sym, "name": (get("name") or "").strip() or None, "session": _date(get("date"))}
        for k in ("price", "change", "pct_change", "iv_rank", "options_volume", "vol_change_1m", "put_volume",
                  "call_volume", "pc_ratio"):
            v, p = _num(get(k))
            vals[k] = v
            if p and k in PERCENT_FIELDS:
                had_pct = True
                vals[k + "_pctsign"] = True
        parsed.append(vals)
    if not parsed:
        raise ImportError_("no valid data rows found")

    if percent_numbers == "auto":
        mode = "percent text" if had_pct else "fractions (0.2 = 20%)"
    else:
        mode = "percent" if percent_numbers == "percent" else "fractions (0.2 = 20%)"
    warnings: list[str] = []
    ivs = [p["iv_rank"] for p in parsed if p.get("iv_rank") is not None and not p.get("iv_rank_pctsign")]
    if mode.startswith("fractions") and ivs and max(ivs) > 1.5:
        warnings.append("Some IV Rank values are above 1.5 but were read as fractions; check the percent setting.")

    # Files that accumulate several days (e.g. a spreadsheet history sheet): keep one session only.
    dates = {p["session"] for p in parsed if p["session"]}
    session_date = session_override or (max(dates) if dates else footer_date)
    if dates and session_date:
        other = [p for p in parsed if p["session"] and p["session"] != session_date]
        if other:
            warnings.append(f"{len(other)} row(s) dated other than {session_date.isoformat()} were ignored "
                            f"(file covers {len(dates)} dates)")
            parsed = [p for p in parsed if not p["session"] or p["session"] == session_date]
    if not parsed:
        raise ImportError_(f"no rows for {session_date} in the file")

    rows: list[ImportedRow] = []
    seen: set[str] = set()
    for p in parsed:
        def pct(k):
            v = p.get(k)
            if v is None:
                return None
            if p.get(k + "_pctsign") or mode == "percent":
                return v
            return v * 100.0

        if p["symbol"] in seen:
            skipped.append(f"duplicate symbol {p['symbol']} (first row kept)")
            continue
        seen.add(p["symbol"])
        put_v, call_v = p.get("put_volume"), p.get("call_volume")
        pcr = p.get("pc_ratio")
        if pcr is None and put_v is not None and call_v:
            pcr = put_v / call_v
        rows.append(ImportedRow(
            symbol=p["symbol"], name=p["name"], price=p.get("price"), change=p.get("change"),
            pct_change=pct("pct_change"), iv_rank=pct("iv_rank"),
            options_volume=None if p.get("options_volume") is None else int(p["options_volume"]),
            vol_change_1m_pct=pct("vol_change_1m"),
            put_volume=None if put_v is None else int(put_v), call_volume=None if call_v is None else int(call_v),
            pc_ratio=pcr, session=p["session"]))

    if session_date is None:
        warnings.append("no date found in the file; the upload date will be used")
    return ParseResult(rows, session_date, mode, skipped, warnings,
                       {k: header[i] for k, i in col.items()})


def to_dict(r: ImportedRow) -> dict:
    d = asdict(r)
    d["session"] = r.session.isoformat() if r.session else None
    return d
