"""Shariah screening — an analytical aid, NOT a religious certification or fatwa.

Two stages, both mandatory:

1. **Business activity screen** — SIC-code exclusions, keyword review triggers and an
   administrator-maintained per-ticker business review (with reviewer, date and notes) for
   mixed-revenue companies.
2. **Financial ratio screen** — thresholds and denominators per methodology (table below).

If any required input is missing or the financial statements are older than the configured
maximum age, the status is **Needs Review** and the stock cannot enter the Top 5.

Methodology parameters are taken from the providers' publicly available methodology summaries
and must be re-verified against the current official documents before production use.

| Method | Denominator | Debt | Cash + interest-bearing securities | Receivables | Non-compliant income |
|---|---|---|---|---|---|
| AAOIFI (Std 21) | market cap | < 30% | < 30% | — | < 5% of revenue |
| DJIM | 24-month avg market cap | < 33% | < 33% | < 33% | business screen only |
| MSCI Islamic | total assets | < 33.33% | < 33.33% | (receivables + cash) < 33.33% | < 5% of revenue |
| S&P Shariah | 36-month avg market cap | < 33% | < 33% | < 49% | < 5% of revenue |
| FTSE Shariah | total assets | < 33.333% | < 33.333% | (receivables + cash) < 50% | < 5% of revenue |

Input definitions (from SEC XBRL, point-in-time by filing date):
  * Interest-bearing debt = long-term debt (current + non-current) + short-term borrowings +
    finance lease liabilities. Operating lease liabilities are excluded (documented choice).
  * Cash + interest-bearing securities = cash & equivalents + short-term investments +
    marketable securities. Conservative: all cash is treated as potentially interest-bearing.
  * Non-compliant income = interest income (+ any admin-entered prohibited segment revenue).
    If only "interest and dividend income" is reported, the combined figure is used
    (conservative overstatement).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.config import ShariahConfig

METHODOLOGIES: dict[str, dict] = {
    "AAOIFI": {"label": "AAOIFI Shari'ah Standard No. 21", "denominator": "market_cap",
               "debt": 30.0, "cash": 30.0, "receivables": None, "receivables_plus_cash": None, "income": 5.0},
    "DJIM": {"label": "Dow Jones Islamic Market Indices", "denominator": "avg_market_cap_24m",
             "debt": 33.0, "cash": 33.0, "receivables": 33.0, "receivables_plus_cash": None, "income": None},
    "MSCI": {"label": "MSCI Islamic Index Series", "denominator": "total_assets",
             "debt": 33.33, "cash": 33.33, "receivables": None, "receivables_plus_cash": 33.33, "income": 5.0},
    "SP": {"label": "S&P Shariah Indices", "denominator": "avg_market_cap_36m",
           "debt": 33.0, "cash": 33.0, "receivables": 49.0, "receivables_plus_cash": None, "income": 5.0},
    "FTSE": {"label": "FTSE Shariah Global Equity Index Series", "denominator": "total_assets",
             "debt": 33.333, "cash": 33.333, "receivables": None, "receivables_plus_cash": 50.0, "income": 5.0},
}

# SIC codes whose primary activity is prohibited under all supported methodologies
PROHIBITED_SIC: dict[str, str] = {
    **{c: "conventional banking / interest-based finance" for c in (
        "6021", "6022", "6029", "6035", "6036", "6111", "6141", "6153", "6159", "6162", "6163", "6172", "6189", "6199")},
    **{c: "conventional insurance" for c in ("6311", "6321", "6324", "6331", "6351", "6361", "6399", "6411")},
    "2082": "alcohol (malt beverages)", "2084": "alcohol (wines)", "2085": "alcohol (distilled spirits)",
    "5180": "alcohol wholesale", "5181": "alcohol wholesale", "5182": "alcohol wholesale",
    "7990": "gambling / casinos", "7993": "gambling (coin-operated)", "2111": "tobacco",
}
# SIC codes that need a manual review (mixed or methodology-dependent activities)
REVIEW_SIC: dict[str, str] = {
    "6211": "securities brokerage (often interest-based)", "6282": "investment advice", "6798": "REIT",
    "6770": "blank check / SPAC (trust holds interest-bearing assets)", "7011": "hotels (may include casinos/alcohol)",
    "2011": "meat packing (pork exposure)", "2013": "sausages / prepared meats (pork exposure)",
    "5812": "restaurants (alcohol / pork exposure)", "5813": "drinking places (alcohol)",
    "3480": "weapons", "3482": "ammunition", "3484": "small arms", "3489": "ordnance",
    "7812": "motion pictures (content review)", "7841": "video rental (content review)",
    "4833": "television broadcasting (content review)", "6726": "investment trusts",
}
REVIEW_KEYWORDS = ("casino", "gaming", "brewing", "brewery", "distill", "winery", "spirits", "bancorp", "bank",
                   "insurance", "lending", "mortgage", "adult", "cannabis", "tobacco", "pork")


@dataclass
class BusinessReview:
    """Manual review recorded by an administrator (stored in DB with audit trail)."""

    status: str  # "compliant" | "non_compliant"
    reviewer: str
    reviewed_on: date
    notes: str
    prohibited_revenue_pct: float | None = None  # mixed revenue: % of revenue from prohibited lines


@dataclass
class Fundamentals:
    ticker: str
    company_name: str
    sic: str | None
    period_end: date | None
    filed: date | None
    source: str  # e.g. "SEC XBRL companyfacts 10-Q 0000320193-26-000010"
    total_assets: float | None
    debt: float | None
    cash_and_securities: float | None
    receivables: float | None
    revenue: float | None
    interest_income: float | None
    interest_income_includes_dividends: bool = False
    market_cap: float | None = None
    avg_market_cap_24m: float | None = None
    avg_market_cap_36m: float | None = None
    shares_outstanding_date: date | None = None  # date of the dei shares-outstanding fact used
    raw: dict = field(default_factory=dict)


@dataclass
class ShariahResult:
    status: str  # "Compliant" | "Non-Compliant" | "Needs Review"
    methodology: str
    methodology_label: str
    screened_on: date
    business: dict
    ratios: list[dict]
    reasons: list[str]
    source: str
    financials_period_end: str | None
    disclaimer: str = ("Analytical screening aid only. Not a religious certification or fatwa. "
                       "Consult a qualified Shariah scholar.")

    def to_dict(self) -> dict:
        return self.__dict__.copy() | {"screened_on": self.screened_on.isoformat()}


def business_screen(f: Fundamentals, review: BusinessReview | None) -> tuple[str, str]:
    """Returns (status, explanation) with status in pass|fail|review."""
    if review:
        if review.status == "non_compliant":
            return "fail", f"Manual review by {review.reviewer} on {review.reviewed_on}: {review.notes}"
        return "pass", f"Manual review by {review.reviewer} on {review.reviewed_on}: {review.notes}"
    if not f.sic:
        return "review", "SIC industry code unavailable"
    if f.sic in PROHIBITED_SIC:
        return "fail", f"SIC {f.sic}: {PROHIBITED_SIC[f.sic]}"
    if f.sic in REVIEW_SIC:
        return "review", f"SIC {f.sic}: {REVIEW_SIC[f.sic]} — needs manual business review"
    name = f.company_name.lower()
    hit = next((k for k in REVIEW_KEYWORDS if k in name), None)
    if hit:
        return "review", f"Company name contains '{hit}' — needs manual business review"
    return "pass", f"SIC {f.sic} is not in a prohibited or review industry"


def screen(f: Fundamentals, cfg: ShariahConfig, as_of: date, review: BusinessReview | None = None) -> ShariahResult:
    m = METHODOLOGIES[cfg.methodology]
    reasons: list[str] = []
    review_needed = False
    failed = False

    b_status, b_expl = business_screen(f, review)
    if b_status == "fail":
        failed = True
        reasons.append("Business activity: " + b_expl)
    elif b_status == "review":
        review_needed = True
        reasons.append("Business activity: " + b_expl)

    if f.filed is None or (as_of - f.filed).days > cfg.max_financials_age_days:
        review_needed = True
        reasons.append(f"Financial statements missing or older than {cfg.max_financials_age_days} days")

    denom_key = m["denominator"]
    denom = getattr(f, denom_key)
    ratios: list[dict] = []

    def ratio(name: str, numerator: float | None, limit: float | None, denominator: float | None, dlabel: str):
        nonlocal review_needed, failed
        if limit is None:
            return
        if numerator is None or not denominator:
            review_needed = True
            reasons.append(f"{name}: missing {'numerator' if numerator is None else dlabel}")
            ratios.append({"name": name, "value": None, "limit": limit, "denominator": dlabel, "status": "missing"})
            return
        v = numerator / denominator * 100
        ok = v < limit
        if not ok:
            failed = True
            reasons.append(f"{name} {v:.2f}% ≥ limit {limit}% (denominator: {dlabel})")
        ratios.append({"name": name, "value": round(v, 3), "limit": limit, "denominator": dlabel,
                       "numerator": numerator, "denominator_value": denominator, "status": "pass" if ok else "fail"})

    dlabel = denom_key.replace("_", " ")
    ratio("Interest-bearing debt", f.debt, m["debt"], denom, dlabel)
    ratio("Cash + interest-bearing securities", f.cash_and_securities, m["cash"], denom, dlabel)
    ratio("Accounts receivable", f.receivables, m["receivables"], denom, dlabel)
    rc = None if f.receivables is None or f.cash_and_securities is None else f.receivables + f.cash_and_securities
    ratio("Receivables + cash", rc, m["receivables_plus_cash"], denom, dlabel)

    nc_income = f.interest_income
    if nc_income is not None and review and review.prohibited_revenue_pct and f.revenue:
        nc_income += f.revenue * review.prohibited_revenue_pct / 100
    ratio("Non-compliant income", nc_income, m["income"], f.revenue, "total revenue")
    if f.interest_income_includes_dividends:
        reasons.append("Note: interest income includes dividends (conservative)")

    status = "Non-Compliant" if failed else "Needs Review" if review_needed else "Compliant"
    return ShariahResult(status=status, methodology=cfg.methodology, methodology_label=m["label"], screened_on=as_of,
                         business={"status": b_status, "explanation": b_expl, "sic": f.sic,
                                   "manual_review": review.__dict__ if review else None},
                         ratios=ratios, reasons=reasons, source=f.source,
                         financials_period_end=f.period_end.isoformat() if f.period_end else None)


def purification(dividend_or_income: float, non_compliant_income_pct: float) -> dict:
    """Amount to donate = income received × non-compliant income ratio (common AAOIFI practice)."""
    amt = dividend_or_income * non_compliant_income_pct / 100
    return {"income": dividend_or_income, "non_compliant_pct": non_compliant_income_pct,
            "purification_amount": round(amt, 2), "retained": round(dividend_or_income - amt, 2)}
