"""OpenInsider screener (http://openinsider.com/screener) — insider purchases parsed from the results table.

OpenInsider republishes SEC Form 4 data. The screener is queried for purchases only (``xp=1``) filed in
the last ``fd`` days; columns are matched by their header text so a re-ordered table still parses:
  X · Filing Date · Trade Date · Ticker · Company Name · Insider Name · Title · Trade Type · Price · Qty ·
  Owned · ΔOwn · Value · 1d · 1w · 1m · 6m
"X" flags: A = amended filing, D = derivative transaction in the filing, E = error detected,
M = multiple transactions in the filing (aggregated, weighted-average price).
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from html.parser import HTMLParser

from app.providers.http import HttpProvider, ProviderError
from app.screening.insider import InsiderTxn
from app.screening.types import Freshness, Provenance

ACCESSION_RE = re.compile(r"(\d{10})-?(\d{2})-?(\d{6})")


class _TableParser(HTMLParser):
    """Collects every <table> as rows of (text, first href) cells, with the table's class attribute."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[dict] = []
        self._stack: list[dict] = []
        self._row: list | None = None
        self._cell: list | None = None
        self._href: str | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._stack.append({"class": a.get("class") or "", "rows": []})
        elif tag == "tr" and self._stack:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell, self._href = [], None
        elif tag == "a" and self._cell is not None and self._href is None:
            self._href = a.get("href")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append((" ".join("".join(self._cell).split()), self._href))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._stack:
            if self._row:
                self._stack[-1]["rows"].append(self._row)
            self._row = None
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _num(s: str | None) -> float | None:
    if not s:
        return None
    t = s.replace("$", "").replace(",", "").replace("+", "").replace("%", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def _date(s: str | None) -> date | None:
    try:
        return date.fromisoformat((s or "").strip()[:10])
    except ValueError:
        return None


def _key(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", h.lower().replace("δ", "delta").replace("Δ", "delta"))


def parse_screener(html: str) -> list[dict]:
    """Rows of the OpenInsider results table as dicts (raw values + parsed fields)."""
    p = _TableParser()
    p.feed(html)
    table = next((t for t in p.tables if "tinytable" in t["class"]), None)
    if table is None:  # fall back to the first table whose header has Ticker and Trade Type
        table = next((t for t in p.tables if t["rows"] and {"ticker", "tradetype"} <= {_key(c[0]) for c in t["rows"][0]}), None)
    if table is None or not table["rows"]:
        return []
    header = [_key(c[0]) for c in table["rows"][0]]
    idx = {h: i for i, h in enumerate(header)}
    need = {"filingdate", "tradedate", "ticker", "insidername", "tradetype", "price", "qty"}
    if not need <= set(idx):
        raise ProviderError("openinsider", f"unexpected table columns: {', '.join(header)}")

    def cell(row, name):
        i = idx.get(name)
        return row[i] if i is not None and i < len(row) else ("", None)

    out = []
    for row in table["rows"][1:]:
        if len(row) < len(need):
            continue
        filing_txt, filing_href = cell(row, "filingdate")
        insider_txt, insider_href = cell(row, "insidername")
        out.append({
            "x": cell(row, "x")[0], "filing_date": _date(filing_txt), "filing_href": filing_href or "",
            "trade_date": _date(cell(row, "tradedate")[0]), "ticker": cell(row, "ticker")[0].strip().upper(),
            "company": cell(row, "companyname")[0], "insider": insider_txt, "insider_href": insider_href or "",
            "title": cell(row, "title")[0], "trade_type": cell(row, "tradetype")[0],
            "price": _num(cell(row, "price")[0]), "qty": _num(cell(row, "qty")[0]),
            "owned": _num(cell(row, "owned")[0]), "value": _num(cell(row, "value")[0]),
        })
    return out


def to_txn(r: dict) -> InsiderTxn | None:
    """Map one screener row to an ``InsiderTxn`` (open-market purchases only)."""
    code = (r["trade_type"] or "").split(" ")[0].strip().upper()[:1]
    if not r["ticker"] or not r["trade_date"] or not r["filing_date"] or r["qty"] is None:
        return None
    m = ACCESSION_RE.search(r["filing_href"])
    accession = f"{m[1]}-{m[2]}-{m[3]}" if m else "OI-" + hashlib.sha1(
        f"{r['ticker']}|{r['insider']}|{r['trade_date']}|{r['qty']}|{r['price']}".encode()).hexdigest()[:20]
    owner = re.search(r"/(\d{4,10})/?$", r["insider_href"])
    owner_cik = owner[1].zfill(10) if owner else hashlib.sha1(r["insider"].lower().encode()).hexdigest()[:10]
    title = r["title"] or ""
    t = title.lower()
    is_dir = "dir" in t
    is_ten = "10%" in t
    tokens = [x.strip() for x in re.split(r"[,;/]", t) if x.strip()]
    is_off = any(x not in ("dir", "director", "10%", "10% owner", "owner") for x in tokens)
    # stable per-row index so re-fetching the same row is recognised as a duplicate
    idx = int(hashlib.sha1(f"{r['trade_date']}|{r['qty']}|{r['price']}|{r['owned']}".encode()).hexdigest()[:7], 16)
    return InsiderTxn(
        accession=accession, txn_index=idx, issuer_cik=r["ticker"][:10], ticker=r["ticker"], owner_name=r["insider"][:255],
        owner_cik=owner_cik, officer_title=title[:255], is_director=is_dir, is_officer=is_off, is_ten_pct_owner=is_ten,
        transaction_date=r["trade_date"], filing_date=r["filing_date"], code=code,
        acquired_disposed="A" if code == "P" else "D", shares=abs(r["qty"]), price=r["price"],
        shares_owned_after=r["owned"], direct_indirect="D", security_title="Common Stock",
        is_amendment="A" in (r["x"] or ""), is_derivative=False,
        footnotes="OpenInsider" + (f" flags {r['x']}" if r["x"] else ""))


class OpenInsider:
    """No API key; requests are paced (1 per 2 s) and cached for 30 minutes."""

    name = "openinsider"
    base = "http://openinsider.com"

    def __init__(self):
        self.http = HttpProvider(self.name, self.base, requests_per_second=0.5, cache_ttl=1800, timeout=40,
                                 headers={"User-Agent": "Mozilla/5.0 (compatible; StockScreener personal research)"})

    def prov(self, endpoint: str = "/screener") -> Provenance:
        return Provenance(self.name, Freshness.EOD, datetime.now(timezone.utc), None, endpoint=endpoint)

    async def purchases(self, filed_days: int = 730, per_page: int = 100, max_pages: int = 1) -> list[InsiderTxn]:
        """The latest open-market purchases (newest filing first). Default: the first 100 results of the
        screener (fd=730, cnt=100, page 1), i.e. one request per scan."""
        out: list[InsiderTxn] = []
        for page in range(1, max_pages + 1):
            url = (f"/screener?s=&o=&pl=&ph=&ll=&lh=&fd={filed_days}&fdr=&td=0&tdr=&fdlyl=&fdlyh=&daysago=&xp=1"
                   f"&vl=&vh=&ocl=&och=&sic1=-1&sicl=100&sich=9999&grp=0&nfl=&nfh=&nil=&nih=&nol=&noh=&v2l=&v2h="
                   f"&oc2l=&oc2h=&sortcol=0&cnt={per_page}&page={page}")
            rows = parse_screener(await self.http.get_text(url))
            out += [t for r in rows if (t := to_txn(r))]
            if len(rows) < per_page:
                break
        return out
