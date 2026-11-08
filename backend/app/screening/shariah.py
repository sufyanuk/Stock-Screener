"""Shariah data structures shared with the SEC provider, and dividend purification.

Classification is done by the internal engine in ``app/screening/shariah_engine.py`` (AAOIFI and
FTSE kept separate; thresholds only from the official documents; missing data → DOUBTFUL).
This module only keeps the per-company SEC figures used by the scan and the purification calculator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


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


def purification(dividend_or_income: float, non_compliant_income_pct: float) -> dict:
    """Amount to donate = income received × non-compliant income ratio (common AAOIFI practice)."""
    amt = dividend_or_income * non_compliant_income_pct / 100
    return {"income": dividend_or_income, "non_compliant_pct": non_compliant_income_pct,
            "purification_amount": round(amt, 2), "retained": round(dividend_or_income - amt, 2)}
