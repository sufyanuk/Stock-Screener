"""Loads SEC financial figures for every listed US stock and runs the internal Shariah engine
(app/screening/shariah_engine.py) for each methodology.

Data (all from SEC EDGAR, free):
* XBRL **frames** (one request returns one fact per company for a tag and calendar period) for the
  balance-sheet and income-statement lines below — about 150 requests for the whole market.
* **Submissions** per company for the industry SIC code (used by the business screen).
* Market capitalisation (only for methodologies that use it) = shares outstanding from the same
  SEC balance sheet × latest price; averages use month-end closes (adjusted) × those shares.

Balance-sheet lines are taken at each company's latest balance-sheet date only (all lines from one
date, never mixed). Income lines come from the latest fiscal year. Nothing is estimated: a line that
is not in the filings stays missing and the affected tests are DOUBTFUL.
"""
from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.db import upsert
from app.models import (BusinessReviewRow, MonthlyClose, PriceSnapshot, SecCompany, SecFact, SecFrameLoad, Security,
                        ShariahResultRow, ShariahRules)
from app.screening import shariah_engine as se

# ------------------------------------------------------------------ XBRL tags (us-gaap)
ASSETS = "Assets"
INSTANT_TAGS = [
    # debt
    "LongTermDebt", "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermNotesPayable",
    "ConvertibleNotesPayable", "LongTermLineOfCredit", "OtherLongTermDebtNoncurrent", "LongTermDebtCurrent",
    "DebtCurrent", "ShortTermBorrowings", "CommercialPaper", "LinesOfCreditCurrent", "NotesPayableCurrent",
    "OtherShortTermBorrowings", "FinanceLeaseLiability", "FinanceLeaseLiabilityNoncurrent", "FinanceLeaseLiabilityCurrent",
    # cash and interest-bearing investments
    "CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash",
    "ShortTermInvestments", "AvailableForSaleSecuritiesDebtSecuritiesCurrent", "MarketableSecuritiesCurrent",
    "MarketableSecuritiesNoncurrent", "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",
    # receivables
    "AccountsReceivableNetCurrent", "ReceivablesNetCurrent", "AccountsNotesAndLoansReceivableNetCurrent",
]
SHARES_TAG = "CommonStockSharesOutstanding"  # unit "shares", same balance-sheet date
REVENUE_TAGS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet"]
INTEREST_TAGS = ["InvestmentIncomeInterest", "InterestIncomeOther", "InterestIncomeOperating", "InterestAndOtherIncome",
                 "InvestmentIncomeInterestAndDividend", "InterestAndDividendIncomeOperating"]
INTEREST_NOTES = {"InterestAndOtherIncome": "interest income figure includes other income (conservative)",
                  "InvestmentIncomeInterestAndDividend": "interest income figure includes dividends (conservative)",
                  "InterestAndDividendIncomeOperating": "interest income figure includes dividends (conservative)"}
DURATION_TAGS = REVENUE_TAGS + INTEREST_TAGS
MAX_AGE_DAYS = 456  # balance sheets older than ~15 months are DOUBTFUL
SIC_BATCH = 16


def _quarters(today: date, n: int = 5) -> list[str]:
    """Most recent completed calendar quarters, newest first: CY2026Q3I, CY2026Q2I, ..."""
    y, q = today.year, (today.month - 1) // 3  # current quarter is not complete
    out = []
    for _ in range(n):
        if q == 0:
            y, q = y - 1, 4
        out.append(f"CY{y}Q{q}I")
        q -= 1
    return out


def _years(today: date) -> list[str]:
    return [f"CY{today.year - 1}", f"CY{today.year - 2}"]


def frame_plan(today: date) -> list[tuple[str, str, str, str]]:
    """(key, tag, unit, period) — Assets first: it fixes each company's balance-sheet date."""
    qs = _quarters(today)
    plan = [(f"{ASSETS}/{p}", ASSETS, "USD", p) for p in qs]
    plan += [(f"{t}/{p}", t, "USD", p) for t in INSTANT_TAGS for p in qs]
    plan += [(f"{SHARES_TAG}/{p}", SHARES_TAG, "shares", p) for p in qs]
    plan += [(f"{t}/{p}", t, "USD", p) for t in DURATION_TAGS for p in _years(today)]
    return plan


def _today() -> date:
    return date.today()


def _cik(v) -> str:
    return str(v).zfill(10)


def _store_frame(db: Session, tag: str, period: str, rows: list[dict], ciks: set[str]) -> int:
    """Keep the facts we need: Assets = latest date per company; other balance-sheet tags only at that
    date; annual tags = latest fiscal year (about 12 months long)."""
    rows = [r for r in rows if _cik(r.get("cik")) in ciks and r.get("val") is not None and r.get("end")]
    if not rows:
        return 0
    have = {f.cik: f for f in db.scalars(select(SecFact).where(SecFact.tag == tag)).all()}
    anchors = None if tag == ASSETS else dict(db.execute(select(SecFact.cik, SecFact.end).where(SecFact.tag == ASSETS)).all())
    duration = period.count("Q") == 0
    out: dict[str, dict] = {}
    for r in rows:
        c, end = _cik(r["cik"]), date.fromisoformat(r["end"])
        start = date.fromisoformat(r["start"]) if r.get("start") else None
        if duration and (start is None or not 340 <= (end - start).days <= 390):
            continue
        if not duration and tag != ASSETS and anchors.get(c) != end:
            continue
        cur = have.get(c)
        if (cur is not None and cur.end > end) or (c in out and out[c]["end"] > end):
            continue
        out[c] = {"cik": c, "tag": tag, "end": end, "start": start, "val": float(r["val"]), "accn": r.get("accn"),
                  "frame": period}
    upsert(db, SecFact, list(out.values()))
    return len(out)


def status(db: Session) -> dict:
    today = _today()
    plan = frame_plan(today)
    done = set(db.scalars(select(SecFrameLoad.key)).all())
    ciks = {_cik(c) for c in db.scalars(select(Security.cik).where(Security.active.is_(True))).all() if c}
    have_sic = set(db.scalars(select(SecCompany.cik)).all())
    last = db.scalar(select(ShariahResultRow.computed_at).order_by(ShariahResultRow.computed_at.desc()).limit(1))
    return {"frames_total": len(plan), "frames_done": len([k for k, *_ in plan if k in done]),
            "companies_total": len(ciks), "companies_done": len(ciks & have_sic),
            "results": db.query(ShariahResultRow).count(), "computed_at": last.isoformat() if last else None,
            "quarters": _quarters(today), "fiscal_years": _years(today)}


async def load(db: Session, ds, max_seconds: float = 200.0, refresh: bool = False) -> dict:
    """Resumable: SEC frames → SIC codes → classification of every stock. Call repeatedly until
    ``complete`` is true (each call stays within ``max_seconds``)."""
    from app.providers.http import ProviderError

    started = time.time()
    sec = getattr(ds, "sec", None)
    if sec is None:
        return status(db) | {"complete": False, "processed": 0, "errors": ["SEC EDGAR is not configured (SEC_USER_AGENT)"]}
    if refresh:
        db.execute(delete(SecFrameLoad))
        db.commit()
    budget = max(10.0, max_seconds - 40)  # keep time to classify
    errors: list[str] = []
    processed = 0
    ciks = {_cik(c) for c in db.scalars(select(Security.cik).where(Security.active.is_(True), Security.cik.is_not(None))).all()}
    done = set(db.scalars(select(SecFrameLoad.key)).all())
    todo = [p for p in frame_plan(_today()) if p[0] not in done]
    i = 0
    while i < len(todo) and time.time() - started < budget and len(errors) < 6:
        group = todo[i:i + 4]
        if any(g[1] == ASSETS for g in group):  # Assets fixes each company's balance-sheet date: store it first
            group = [g for g in group if g[1] == ASSETS]
        i += len(group)
        try:
            res = await asyncio.wait_for(asyncio.gather(*(sec.frame("us-gaap", t, u, p) for _, t, u, p in group),
                                                        return_exceptions=True), max(5.0, budget - (time.time() - started)))
        except asyncio.TimeoutError:
            break  # out of time: the next call continues with these frames
        for (key, tag, _, period), rows in zip(group, res):
            if isinstance(rows, Exception):
                errors.append(f"{key}: {str(rows)[:160]}")
                continue
            n = _store_frame(db, tag, period, rows, ciks)
            upsert(db, SecFrameLoad, [{"key": key, "rows": n, "loaded_at": datetime.now(timezone.utc)}])
            processed += 1
        db.commit()
    # industry codes (SIC) for the business screen
    need = sorted(ciks - set(db.scalars(select(SecCompany.cik)).all()))
    for j in range(0, len(need), SIC_BATCH):
        if time.time() - started > budget or len(errors) > 20:
            break
        part = need[j:j + SIC_BATCH]
        try:
            res = await asyncio.wait_for(asyncio.gather(*(sec.submissions(c) for c in part), return_exceptions=True),
                                         max(5.0, budget - (time.time() - started)))
        except asyncio.TimeoutError:
            break
        comp_rows = []
        for c, sub in zip(part, res):
            if isinstance(sub, ProviderError) and sub.status == 404:
                sub = {}  # not on EDGAR: recorded without an industry code (business screen → DOUBTFUL)
            if isinstance(sub, Exception):
                errors.append(f"submissions {c}: {str(sub)[:120]}")
                continue
            comp_rows.append({"cik": c, "name": (sub.get("name") or "")[:255] or None, "sic": sub.get("sic") or None,
                              "sic_description": sub.get("sicDescription") or None,
                              "fetched_at": datetime.now(timezone.utc)})
            processed += 1
        upsert(db, SecCompany, comp_rows)
        db.commit()
    st = status(db)
    complete = st["frames_done"] == st["frames_total"] and st["companies_done"] >= st["companies_total"]
    # only classify once every frame is in: a half-loaded balance sheet could understate debt or cash
    frames_complete = st["frames_done"] == st["frames_total"]
    if frames_complete and (complete or processed == 0 or not st["results"]):
        compute_all(db)
        st = status(db)
    return st | {"complete": complete, "processed": processed, "errors": errors[:5]}


# ------------------------------------------------------------------ building engine inputs
def _first(f: dict, tags: list[str]) -> tuple[float | None, str | None]:
    for t in tags:
        if t in f:
            return f[t].val, t
    return None, None


def build_inputs(sec: Security, comp: SecCompany | None, facts: dict[str, SecFact], price: float | None,
                 closes: list[float]) -> se.Inputs:
    anchor = facts.get(ASSETS)
    end = anchor.end if anchor else None
    bs = {t: f for t, f in facts.items() if f.start is None and (t == ASSETS or f.end == end)}
    used: dict[str, dict] = {}

    def take(name: str, tags: list[str]) -> float | None:
        v, t = _first(bs, tags)
        if t:
            used.setdefault(name, []).append({"tag": t, "value": v, "end": bs[t].end.isoformat(), "accn": bs[t].accn})
        return v

    def add(*vals):
        got = [v for v in vals if v is not None]
        return sum(got) if got else None

    # interest-bearing debt (long-term incl. current portion + short-term borrowings + finance leases)
    ltd = take("debt", ["LongTermDebt"])
    ltd_current_known = ltd is not None
    if ltd is None:
        nc = take("debt", ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermNotesPayable",
                           "ConvertibleNotesPayable", "LongTermLineOfCredit", "OtherLongTermDebtNoncurrent"])
        cur = take("debt", ["LongTermDebtCurrent"])
        ltd_current_known = cur is not None
        ltd = add(nc, cur)
    st = take("debt", ["ShortTermBorrowings", "CommercialPaper", "LinesOfCreditCurrent", "NotesPayableCurrent",
                       "OtherShortTermBorrowings"])
    if st is None and not ltd_current_known:
        st = take("debt", ["DebtCurrent"])  # short-term debt + current maturities in one line
    fl = take("debt", ["FinanceLeaseLiability"])
    if fl is None:
        fl = add(take("debt", ["FinanceLeaseLiabilityNoncurrent"]), take("debt", ["FinanceLeaseLiabilityCurrent"]))
    debt = add(ltd, st, fl)

    cash = take("cash_ib", ["CashAndCashEquivalentsAtCarryingValue",
                            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"])
    cash_ib = None
    if cash is not None:
        cash_ib = add(cash, take("cash_ib", ["ShortTermInvestments", "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
                                             "MarketableSecuritiesCurrent"]),
                      take("cash_ib", ["MarketableSecuritiesNoncurrent", "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent"]))
    recv = take("receivables", ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent",
                                "AccountsNotesAndLoansReceivableNetCurrent"])
    shares = bs[SHARES_TAG].val if SHARES_TAG in bs else None

    annual = {t: f for t, f in facts.items() if f.start is not None}

    def latest(tags: list[str]):
        cands = [(annual[t].end, -i, t) for i, t in enumerate(tags) if t in annual]
        if not cands:
            return None, None
        _, _, t = max(cands)
        return annual[t], t

    rev, rev_tag = latest(REVENUE_TAGS)
    intr, intr_tag = latest(INTEREST_TAGS)
    for name, f, t in (("revenue", rev, rev_tag), ("interest_income", intr, intr_tag)):
        if f:
            used[name] = [{"tag": t, "value": f.val, "end": f.end.isoformat(), "start": f.start.isoformat(), "accn": f.accn}]
    if anchor:
        used["total_assets"] = [{"tag": ASSETS, "value": anchor.val, "end": anchor.end.isoformat(), "accn": anchor.accn}]

    notes = []
    mcap = shares * price if shares and price else None

    def avg(n: int):
        if not shares or len(closes) < n:
            return None
        return sum(closes[-n:]) / n * shares

    if shares:
        used["shares_outstanding"] = [{"tag": SHARES_TAG, "value": shares, "end": bs[SHARES_TAG].end.isoformat(),
                                       "accn": bs[SHARES_TAG].accn}]
        notes.append("Average market cap = average month-end close (split/dividend-adjusted) × shares outstanding "
                     "on the latest SEC balance sheet.")
    if rev is not None and end and (end - rev.end).days > 400:
        notes.append(f"Latest annual revenue ({rev.end}) is older than the balance sheet ({end}).")
    return se.Inputs(ticker=sec.ticker, name=(comp.name if comp and comp.name else sec.name) or sec.ticker,
                     sic=(comp.sic if comp else None) or sec.sic, sic_description=comp.sic_description if comp else None,
                     period_end=end, total_assets=anchor.val if anchor else None, debt=debt, cash_ib=cash_ib,
                     receivables=recv, revenue=rev.val if rev else None, revenue_period_end=rev.end if rev else None,
                     interest_income=intr.val if intr else None,
                     interest_income_note=INTEREST_NOTES.get(intr_tag) if intr_tag else None,
                     market_cap=mcap, avg_market_cap_12m=avg(12), avg_market_cap_24m=avg(24), avg_market_cap_36m=avg(36),
                     sources=used, notes=notes)


def rules_overrides(db: Session) -> dict:
    out = {}
    for m in se.METHOD_KEYS:
        r = db.scalars(select(ShariahRules).where(ShariahRules.methodology == m).order_by(ShariahRules.id.desc()).limit(1)).first()
        if r:
            out[m] = r.rules
    return out


def reviews(db: Session, symbols: set[str] | None = None) -> dict[str, se.Review]:
    q = select(BusinessReviewRow).where(BusinessReviewRow.superseded.is_(False)).order_by(BusinessReviewRow.id)
    if symbols is not None:
        q = q.where(BusinessReviewRow.ticker.in_(symbols))
    return {r.ticker: se.Review(r.status, r.reviewer, r.reviewed_on, r.notes, r.prohibited_revenue_pct)
            for r in db.scalars(q).all()}


def _last_complete_month(today: date) -> date:
    return date(today.year - 1, 12, 1) if today.month == 1 else date(today.year, today.month - 1, 1)


def _market(db: Session, symbols: list[str], today: date) -> tuple[dict[str, float], dict[str, list[float]]]:
    prices = dict(db.execute(select(PriceSnapshot.symbol, PriceSnapshot.close).where(PriceSnapshot.symbol.in_(symbols))).all())
    first = _last_complete_month(today)
    since = date(first.year - 3, first.month, 1)
    closes: dict[str, list[tuple[date, float]]] = {}
    for s, m, c in db.execute(select(MonthlyClose.symbol, MonthlyClose.month, MonthlyClose.close)
                              .where(MonthlyClose.symbol.in_(symbols), MonthlyClose.month > since,
                                     MonthlyClose.month <= first)).all():
        closes.setdefault(s, []).append((m, c))
    out: dict[str, list[float]] = {}
    for s, rows in closes.items():
        rows.sort()
        # only consecutive months ending at the last complete month count towards an average
        seq, expect = [], first
        for m, c in reversed(rows):
            if m != expect:
                break
            seq.append(c)
            expect = date(m.year - 1, 12, 1) if m.month == 1 else date(m.year, m.month - 1, 1)
        out[s] = list(reversed(seq))
        if s not in prices and seq:
            prices[s] = seq[-1]
    return prices, out


def screen_symbols(db: Session, symbols: list[str] | None = None, today: date | None = None) -> dict[str, dict]:
    """Full results (all methodologies) for the given stocks, from the stored SEC data."""
    today = today or _today()
    q = select(Security).where(Security.active.is_(True))
    if symbols is not None:
        q = q.where(Security.ticker.in_(symbols))
    secs = db.scalars(q).all()
    ciks = {_cik(s.cik) for s in secs if s.cik}
    comps = {c.cik: c for c in db.scalars(select(SecCompany).where(SecCompany.cik.in_(ciks))).all()} if ciks else {}
    facts: dict[str, dict[str, SecFact]] = {}
    for f in (db.scalars(select(SecFact).where(SecFact.cik.in_(ciks))).all() if ciks else []):
        facts.setdefault(f.cik, {})[f.tag] = f
    prices, closes = _market(db, [s.ticker for s in secs], today)
    rules = se.methodologies(rules_overrides(db))
    rev = reviews(db, {s.ticker for s in secs})
    out = {}
    for s in secs:
        c = _cik(s.cik) if s.cik else None
        inp = build_inputs(s, comps.get(c), facts.get(c, {}), prices.get(s.ticker), closes.get(s.ticker, []))
        if not s.cik:
            inp.notes.append("No SEC registration (CIK) for this ticker")
        out[s.ticker] = {"symbol": s.ticker, "cik": c, "inputs": inp,
                         "methods": {m: se.screen(m, rules[m], inp, today, rev.get(s.ticker), MAX_AGE_DAYS)
                                     for m in se.METHOD_KEYS}}
    return out


def compute_all(db: Session) -> int:
    res = screen_symbols(db)
    now = datetime.now(timezone.utc)
    rows = []
    for sym, r in res.items():
        inp: se.Inputs = r["inputs"]
        compact = {"name": inp.name, "sic": inp.sic, "sic_description": inp.sic_description,
                   "period_end": inp.period_end.isoformat() if inp.period_end else None,
                   "methods": {m: {"status": x["status"], "reasons": x["reasons"][:4],
                                   "ratios": [{"key": t["key"], "label": t["label"], "value": t["value"],
                                               "limit": t["limit"], "op": t["op"], "status": t["status"]} for t in x["ratios"]],
                                   "business": x["business"]["status"]}
                               for m, x in r["methods"].items()}}
        st = {m.lower(): r["methods"][m]["status"] for m in se.METHOD_KEYS}
        rows.append({"symbol": sym, "cik": r["cik"], **st, "data": compact, "computed_at": now})
    db.execute(delete(ShariahResultRow))
    for i in range(0, len(rows), 1000):
        db.execute(insert(ShariahResultRow), rows[i:i + 1000])
    db.commit()
    return len(rows)


def detail(db: Session, symbol: str) -> dict | None:
    r = screen_symbols(db, [symbol.upper()]).get(symbol.upper())
    if not r:
        return None
    inp: se.Inputs = r["inputs"]
    return {"symbol": r["symbol"], "cik": r["cik"], "name": inp.name, "sic": inp.sic, "sic_description": inp.sic_description,
            "period_end": inp.period_end.isoformat() if inp.period_end else None,
            "revenue_period_end": inp.revenue_period_end.isoformat() if inp.revenue_period_end else None,
            "sources": inp.sources, "notes": inp.notes, "methods": r["methods"],
            "labels": se.LABELS, "disclaimer": se.DISCLAIMER}


def status_map(db: Session, symbols: set[str], methodology: str) -> dict[str, str]:
    """Legacy colour statuses (Compliant / Non-Compliant / Needs Review) for one methodology."""
    col = {"AAOIFI": ShariahResultRow.aaoifi, "DJIM": ShariahResultRow.djim, "FTSE": ShariahResultRow.ftse,
           "SP": ShariahResultRow.sp}.get(methodology)
    if col is None or not symbols:
        return {}
    return {s: se.LEGACY[v] for s, v in db.execute(select(ShariahResultRow.symbol, col)
                                                    .where(ShariahResultRow.symbol.in_(symbols))).all()}


def is_stale(db: Session, days: int = 7) -> bool:
    last = db.scalar(select(ShariahResultRow.computed_at).order_by(ShariahResultRow.computed_at.desc()).limit(1))
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last > timedelta(days=days)
