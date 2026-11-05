"""SEC EDGAR — official, free source for insider filings (Form 4), company profile (SIC) and
XBRL financial statements. Fair-access policy: ≤ 10 requests/second and a User-Agent that
identifies the application and a contact e-mail (https://www.sec.gov/os/accessing-edgar-data).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from defusedxml import ElementTree as ET

from app.config import get_settings
from app.providers.http import HttpProvider, NotConfigured
from app.screening.insider import InsiderTxn
from app.screening.shariah import Fundamentals
from app.screening.types import Freshness, Provenance

US_EXCHANGES = {"NYSE", "Nasdaq", "NYSE American", "NYSE MKT", "AMEX", "NYSE Arca"}


def _ua() -> str:
    ua = get_settings().sec_user_agent
    if not ua or "@" not in ua:
        raise NotConfigured("sec", "SEC_USER_AGENT must be set to '<App name> <contact e-mail>'")
    return ua


class SecEdgar:
    name = "sec_edgar"

    def __init__(self):
        ua = _ua()
        h = {"User-Agent": ua, "Accept-Encoding": "gzip, deflate"}
        self.www = HttpProvider("sec_edgar", "https://www.sec.gov", requests_per_second=8, headers=h, cache_ttl=3600)
        self.data = HttpProvider("sec_edgar", "https://data.sec.gov", requests_per_second=8, headers=h, cache_ttl=3600)

    def prov(self, endpoint: str, market_ts: datetime | None = None) -> Provenance:
        return Provenance(self.name, Freshness.FILING, datetime.now(timezone.utc), market_ts, endpoint=endpoint)

    async def universe(self) -> list[dict]:
        """All SEC-registered tickers listed on NYSE / Nasdaq / NYSE American."""
        j = await self.www.get_json("/files/company_tickers_exchange.json", cache_ttl=86400)
        fields = j["fields"]
        rows = [dict(zip(fields, r)) for r in j["data"]]
        return [{"cik": str(r["cik"]).zfill(10), "ticker": r["ticker"], "name": r["name"], "exchange": r["exchange"]}
                for r in rows if r.get("exchange") in US_EXCHANGES]

    async def submissions(self, cik: str) -> dict:
        return await self.data.get_json(f"/submissions/CIK{cik.zfill(10)}.json", cache_ttl=3600)

    async def companyfacts(self, cik: str) -> dict:
        return await self.data.get_json(f"/api/xbrl/companyfacts/CIK{cik.zfill(10)}.json", cache_ttl=6 * 3600)

    async def form4_index(self, day: date) -> list[dict]:
        """Form 4 / 4/A filings accepted on ``day`` (EDGAR daily form index)."""
        q = (day.month - 1) // 3 + 1
        txt = await self.www.get_text(f"/Archives/edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx")
        return parse_form_index(txt, {"4", "4/A"})

    async def filing_text(self, file_name: str) -> str:
        return await self.www.get_text("/Archives/" + file_name.removeprefix("/"))

    async def recent_form4_for_issuer(self, cik: str, since: date) -> list[InsiderTxn]:
        sub = await self.submissions(cik)
        r = sub.get("filings", {}).get("recent", {})
        out: list[InsiderTxn] = []
        for acc, form, fdate in zip(r.get("accessionNumber", []), r.get("form", []), r.get("filingDate", [])):
            if form not in ("4", "4/A") or date.fromisoformat(fdate) < since:
                continue
            txt = await self.filing_text(f"edgar/data/{int(cik)}/{acc}.txt")
            out.extend(parse_form4(txt, acc, date.fromisoformat(fdate)))
        return out


def parse_form_index(txt: str, forms: set[str]) -> list[dict]:
    out = []
    started = False
    for line in txt.splitlines():
        if line.startswith("-----"):
            started = True
            continue
        if not started or not line.strip():
            continue
        m = re.match(r"^(\S+(?: \S+)?)\s{2,}(.+?)\s{2,}(\d+)\s+(\d{8})\s+(\S+)\s*$", line)
        if m and m.group(1) in forms:
            out.append({"form": m.group(1), "company": m.group(2).strip(), "cik": m.group(3),
                        "filed": datetime.strptime(m.group(4), "%Y%m%d").date(), "file": m.group(5)})
    # an ownership filing is listed once per filer (issuer and each owner): unique by accession
    seen, uniq = set(), []
    for r in out:
        acc = r["file"].rsplit("/", 1)[-1].removesuffix(".txt")
        if acc not in seen:
            seen.add(acc)
            uniq.append(r | {"accession": acc})
    return uniq


def _t(el, path: str) -> str | None:
    x = el.find(path)
    if x is None:
        return None
    v = x.find("value")
    s = (v.text if v is not None else x.text) or ""
    return s.strip() or None


def _f(el, path: str) -> float | None:
    s = _t(el, path)
    try:
        return float(s) if s is not None else None
    except ValueError:
        return None


def parse_form4(text: str, accession: str, filing_date: date) -> list[InsiderTxn]:
    """Parse an ownershipDocument (raw XML or full .txt submission). One row per transaction per
    reporting owner (joint filings list several owners for the same transactions)."""
    m = re.search(r"<ownershipDocument>.*?</ownershipDocument>", text, re.S)
    if not m:
        return []
    root = ET.fromstring(m.group(0))
    doc_type = _t(root, "documentType") or "4"
    issuer_cik = (_t(root, "issuer/issuerCik") or "").zfill(10)
    ticker = (_t(root, "issuer/issuerTradingSymbol") or "").upper()
    owners = []
    for ro in root.findall("reportingOwner"):
        rel = ro.find("reportingOwnerRelationship")
        flag = lambda p: (_t(rel, p) or "0").lower() in ("1", "true") if rel is not None else False
        owners.append({"cik": _t(ro, "reportingOwnerId/rptOwnerCik") or "", "name": _t(ro, "reportingOwnerId/rptOwnerName") or "",
                       "director": flag("isDirector"), "officer": flag("isOfficer"), "ten": flag("isTenPercentOwner"),
                       "title": (_t(rel, "officerTitle") if rel is not None else None) or ""})
    footnotes = " ".join((f.text or "").strip() for f in root.findall("footnotes/footnote"))
    rows: list[InsiderTxn] = []
    idx = 0
    for table, deriv in (("nonDerivativeTable/nonDerivativeTransaction", False),
                         ("derivativeTable/derivativeTransaction", True)):
        for tx in root.findall(table):
            idx += 1
            tdate = _t(tx, "transactionDate")
            if not tdate:
                continue
            for o in owners:
                rows.append(InsiderTxn(
                    accession=accession, txn_index=idx, issuer_cik=issuer_cik, ticker=ticker,
                    owner_name=o["name"], owner_cik=o["cik"], officer_title=o["title"], is_director=o["director"],
                    is_officer=o["officer"], is_ten_pct_owner=o["ten"],
                    transaction_date=date.fromisoformat(tdate[:10]), filing_date=filing_date,
                    code=_t(tx, "transactionCoding/transactionCode") or "",
                    acquired_disposed=_t(tx, "transactionAmounts/transactionAcquiredDisposedCode") or "",
                    shares=_f(tx, "transactionAmounts/transactionShares") or 0.0,
                    price=_f(tx, "transactionAmounts/transactionPricePerShare"),
                    shares_owned_after=_f(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction"),
                    direct_indirect=_t(tx, "ownershipNature/directOrIndirectOwnership") or "D",
                    security_title=_t(tx, "securityTitle") or "", is_amendment=doc_type.endswith("/A"),
                    is_derivative=deriv, footnotes=footnotes[:500]))
    return rows


# ------------------------------------------------------------------ XBRL fundamentals
DEBT_TAGS = [["LongTermDebtNoncurrent", "LongTermDebt"], ["LongTermDebtCurrent", "DebtCurrent"],
             ["ShortTermBorrowings", "CommercialPaper"], ["FinanceLeaseLiability"]]
CASH_TAGS = [["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
             ["ShortTermInvestments", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"], ["MarketableSecuritiesCurrent"]]
REVENUE_TAGS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"]
INTEREST_TAGS = ["InvestmentIncomeInterest", "InterestIncomeOther", "InterestIncomeOperating"]
INTEREST_DIV_TAGS = ["InvestmentIncomeInterestAndDividend"]


def _facts(cf: dict, tag: str, unit: str = "USD", ns: str = "us-gaap") -> list[dict]:
    return cf.get("facts", {}).get(ns, {}).get(tag, {}).get("units", {}).get(unit, [])


def latest_instant(cf: dict, tags: list[str], as_of: date, unit="USD", ns="us-gaap") -> dict | None:
    best = None
    for tag in tags:
        for f in _facts(cf, tag, unit, ns):
            if "start" in f or date.fromisoformat(f["filed"]) > as_of:
                continue
            key = (f["end"], f["filed"])
            if best is None or key > (best["end"], best["filed"]):
                best = f | {"tag": tag}
        if best:
            return best  # first tag in preference order that has data
    return None


def latest_annual_duration(cf: dict, tags: list[str], as_of: date) -> dict | None:
    best = None
    for tag in tags:
        for f in _facts(cf, tag):
            if "start" not in f or date.fromisoformat(f["filed"]) > as_of:
                continue
            days = (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days
            if not 350 <= days <= 380:
                continue
            key = (f["end"], f["filed"])
            if best is None or key > (best["end"], best["filed"]):
                best = f | {"tag": tag}
        if best:
            return best
    return None


def shares_outstanding(cf: dict, as_of: date) -> tuple[float | None, date | None]:
    f = latest_instant(cf, ["EntityCommonStockSharesOutstanding"], as_of, unit="shares", ns="dei")
    if not f:
        return None, None
    # Multi-class issuers report one fact per class with the same end date: sum them.
    same = [x for x in _facts(cf, "EntityCommonStockSharesOutstanding", "shares", "dei")
            if x["end"] == f["end"] and x.get("accn") == f.get("accn")]
    return float(sum(x["val"] for x in same)), date.fromisoformat(f["end"])


def fundamentals_from_xbrl(cf: dict, sub: dict, ticker: str, as_of: date, market_cap: float | None,
                           avg_mcap_24m: float | None = None, avg_mcap_36m: float | None = None) -> Fundamentals:
    used: dict = {}

    def sum_groups(groups: list[list[str]], required_first: bool) -> float | None:
        total, any_found = 0.0, False
        for i, g in enumerate(groups):
            f = latest_instant(cf, g, as_of)
            if f:
                total += f["val"]
                any_found = True
                used[f["tag"]] = {"val": f["val"], "end": f["end"], "accn": f.get("accn"), "form": f.get("form")}
            elif i == 0 and required_first:
                return None
        return total if any_found else None

    debt = sum_groups(DEBT_TAGS, required_first=False)
    cash = sum_groups(CASH_TAGS, required_first=True)
    assets = latest_instant(cf, ["Assets"], as_of)
    recv = latest_instant(cf, ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"], as_of)
    rev = latest_annual_duration(cf, REVENUE_TAGS, as_of)
    intr = latest_annual_duration(cf, INTEREST_TAGS, as_of)
    incl_div = False
    if intr is None:
        intr = latest_annual_duration(cf, INTEREST_DIV_TAGS, as_of)
        incl_div = intr is not None
    for f in (assets, recv, rev, intr):
        if f:
            used[f["tag"]] = {"val": f["val"], "end": f["end"], "accn": f.get("accn"), "form": f.get("form")}
    anchor = assets or recv
    return Fundamentals(
        ticker=ticker, company_name=sub.get("name", ticker), sic=sub.get("sic") or None,
        period_end=date.fromisoformat(anchor["end"]) if anchor else None,
        filed=date.fromisoformat(anchor["filed"]) if anchor else None,
        source=f"SEC XBRL companyfacts ({anchor.get('form')} {anchor.get('accn')})" if anchor else "SEC XBRL companyfacts",
        total_assets=assets["val"] if assets else None, debt=debt, cash_and_securities=cash,
        receivables=recv["val"] if recv else None, revenue=rev["val"] if rev else None,
        interest_income=intr["val"] if intr else None, interest_income_includes_dividends=incl_div,
        market_cap=market_cap, avg_market_cap_24m=avg_mcap_24m, avg_market_cap_36m=avg_mcap_36m, raw=used)
