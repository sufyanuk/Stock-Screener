"""Internal Shariah screening engine — an analytical aid, NOT a religious certification or fatwa.

Two methodologies are supported and kept fully separate: AAOIFI Shari'ah Standard No. 21 and FTSE
Shariah. Each has its own business-activity exclusions and
its own financial tests (numerator, denominator, threshold and comparison). Thresholds are never mixed
between methodologies and are never guessed:

* A methodology only classifies stocks once its rules carry an official ``source`` (document name and
  version). Built-in rules exist only where they were read from the official document.
* Until then every stock is DOUBTFUL / INSUFFICIENT DATA for that methodology (ratios are still shown).
* Any missing input needed by a test makes the result DOUBTFUL — never HALAL.

Results: ``HALAL`` (Shariah compliant), ``NON_COMPLIANT`` or ``DOUBTFUL`` (doubtful / insufficient data).
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from datetime import date

HALAL, NON_COMPLIANT, DOUBTFUL = "HALAL", "NON_COMPLIANT", "DOUBTFUL"
LABELS = {HALAL: "HALAL / SHARIAH COMPLIANT", NON_COMPLIANT: "NON-COMPLIANT", DOUBTFUL: "DOUBTFUL / INSUFFICIENT DATA"}
LEGACY = {HALAL: "Compliant", NON_COMPLIANT: "Non-Compliant", DOUBTFUL: "Needs Review"}  # colouring elsewhere in the app
DISCLAIMER = ("Analytical screening aid only — not a religious certification or fatwa. "
              "Consult a qualified Shariah scholar.")

# ------------------------------------------------------------------ inputs a test can use
NUMERATORS = {
    "debt": "Interest-bearing debt",
    "cash_ib": "Cash + interest-bearing investments",
    "receivables": "Accounts receivable",
    "receivables_plus_cash": "Accounts receivable + cash + interest-bearing investments",
    "interest_income": "Interest income",
    "nonpermissible_income": "Interest income + non-compliant activity income",
}
DENOMINATORS = {
    "total_assets": "Total assets",
    "market_cap": "Market capitalisation (latest price × shares outstanding)",
    "avg_market_cap_12m": "12-month average market capitalisation",
    "avg_market_cap_24m": "24-month average market capitalisation",
    "avg_market_cap_36m": "36-month average market capitalisation",
    "revenue": "Total revenue",
}
OPS = {"lt": "<", "le": "≤"}

# ------------------------------------------------------------------ business activities
# Primary SIC codes (SEC industry classification). "prohibited": the primary business is the activity.
# "review": the activity is often present (mixed revenue) — needs a manual business review.
CATEGORIES: dict[str, dict] = {
    "conventional_finance": {
        "label": "Conventional banking / interest-based finance",
        "prohibited": {"6021", "6022", "6029", "6035", "6036", "6111", "6141", "6153", "6159", "6162", "6163", "6189", "6199"},
        # 6798 REITs are not excluded as such: mortgage REITs fail the debt / interest-income tests
        "review": {"6211", "6282", "6726", "6770", "6799"},
        "keywords": ("bancorp", "bancshares", "bank", "savings", "lending", "mortgage", "financial corp",
                     "municipal", "muni", "bond fund", "income fund", "debt fund", "credit fund", "treasury"),
    },
    "conventional_insurance": {
        "label": "Conventional insurance",
        "prohibited": {"6311", "6321", "6324", "6331", "6351", "6361", "6399"},
        "review": {"6411"},
        "keywords": ("insurance", "assurance", "reinsurance"),
    },
    "alcohol": {
        "label": "Alcohol",
        "prohibited": {"2082", "2084", "2085", "5180", "5181", "5182"},
        "review": {"5812", "5813", "5921", "2086"},
        "keywords": ("brewing", "brewery", "brewer", "distill", "winery", "wine", "spirits", "beer", "liquor"),
    },
    "gambling": {
        "label": "Gambling / casinos",
        "prohibited": set(),
        "review": {"7990", "7993", "7011", "7948"},
        "keywords": ("casino", "gaming", "betting", "lottery", "wagering", "sportsbook"),
    },
    "pork": {
        "label": "Pork / non-halal food",
        "prohibited": set(),
        "review": {"2011", "2013", "2015", "2099", "5147"},
        "keywords": ("pork", "swine", "bacon"),
    },
    "tobacco": {
        "label": "Tobacco",
        "prohibited": {"2111", "2121", "2131", "2141", "5194"},
        "review": set(),
        "keywords": ("tobacco", "cigar", "cigarette", "vapor", "vape", "cannabis"),
    },
    "adult_entertainment": {
        "label": "Adult entertainment / pornography",
        "prohibited": set(),
        "review": set(),
        "keywords": ("adult entertainment", "playboy", "gentlemen's club", "gentlemens club"),
    },
    # methodology-specific categories (used only where the methodology lists them)
    "entertainment": {
        "label": "Entertainment (cinema, music, hotels)",
        "prohibited": set(),
        "review": {"7011", "7812", "7819", "7822", "7829", "7830", "7832", "7900", "7911", "7922", "7929", "3652"},
        "keywords": ("cinema", "theatres", "theaters", "music", "records", "hotel", "resorts", "entertainment"),
    },
    "weapons": {
        "label": "Weapons, arms and defence manufacturing",
        "prohibited": {"3480", "3482", "3483", "3484", "3489"},
        "review": {"3795", "3812", "3721", "3761"},
        "keywords": ("defense", "defence", "firearms", "ammunition", "armament", "weapons"),
    },
}
CORE = ["conventional_finance", "conventional_insurance", "alcohol", "gambling", "pork", "tobacco", "adult_entertainment"]

# ------------------------------------------------------------------ methodologies
# Built-in rules only where read from the official document (``source``). The others start with no
# financial tests and no source: they are fully configurable but classify nothing until configured.
BUILTIN: dict[str, dict] = {
    "AAOIFI": {
        "label": "AAOIFI Shari'ah Standard No. 21", "source": None, "business": list(CORE),
        "business_tolerance_pct": None, "tests": [],
    },
    "FTSE": {
        "label": "FTSE Shariah (FTSE Yasaar Global Equity Shariah Index Series)",
        "source": "FTSE Yasaar Global Equity Shariah Index Series Ground Rules v4.6 (February 2026), rules 4.3.1-4.3.2",
        "business": CORE + ["entertainment", "weapons"],
        "business_tolerance_pct": 5.0,  # 4.3.1: non-permitted sectors "should not exceed 5% of total revenue"
        "tests": [
            {"key": "debt", "label": "Debt / total assets", "numerator": "debt", "denominator": "total_assets",
             "op": "lt", "limit": 33.333},
            {"key": "cash", "label": "Cash and interest-bearing items / total assets", "numerator": "cash_ib",
             "denominator": "total_assets", "op": "lt", "limit": 33.333},
            {"key": "recv_cash", "label": "Accounts receivable and cash / total assets",
             "numerator": "receivables_plus_cash", "denominator": "total_assets", "op": "lt", "limit": 50.0},
            {"key": "income", "label": "Interest and non-compliant activities income / total revenue",
             "numerator": "nonpermissible_income", "denominator": "revenue", "op": "le", "limit": 5.0},
        ],
    },
}
METHOD_KEYS = list(BUILTIN)


def methodologies(overrides: dict | None = None) -> dict[str, dict]:
    """Built-in rules with administrator-entered rules (from the official documents) applied on top."""
    out = copy.deepcopy(BUILTIN)
    for k, v in (overrides or {}).items():
        if k in out and isinstance(v, dict):
            out[k].update({f: v[f] for f in ("source", "business", "business_tolerance_pct", "tests") if f in v})
    return out


def validate_rules(rules: dict) -> list[str]:
    """Problems with administrator-entered rules for one methodology (empty list = valid)."""
    errs = []
    if rules.get("tests") and not (rules.get("source") or "").strip():
        errs.append("Enter the official document (name and version) the thresholds come from.")
    for c in rules.get("business") or []:
        if c not in CATEGORIES:
            errs.append(f"Unknown business category {c}")
    tol = rules.get("business_tolerance_pct")
    if tol is not None and not (0 <= float(tol) <= 100):
        errs.append("Business revenue tolerance must be 0-100%")
    for t in rules.get("tests") or []:
        if t.get("numerator") not in NUMERATORS:
            errs.append(f"Unknown numerator {t.get('numerator')}")
        if t.get("denominator") not in DENOMINATORS:
            errs.append(f"Unknown denominator {t.get('denominator')}")
        if t.get("op") not in OPS:
            errs.append("Comparison must be 'lt' (<) or 'le' (≤)")
        try:
            if not 0 < float(t.get("limit")) <= 100:
                errs.append("Threshold must be between 0 and 100%")
        except (TypeError, ValueError):
            errs.append("Threshold must be a number")
    return errs


# ------------------------------------------------------------------ screening
@dataclass
class Inputs:
    """Figures from SEC filings (XBRL) for one company, plus market prices. ``None`` = not reported."""

    ticker: str
    name: str
    sic: str | None = None
    sic_description: str | None = None
    period_end: date | None = None  # balance-sheet date
    total_assets: float | None = None
    debt: float | None = None
    cash_ib: float | None = None
    receivables: float | None = None
    revenue: float | None = None
    revenue_period_end: date | None = None
    interest_income: float | None = None
    interest_income_note: str | None = None
    market_cap: float | None = None
    avg_market_cap_12m: float | None = None
    avg_market_cap_24m: float | None = None
    avg_market_cap_36m: float | None = None
    sources: dict = field(default_factory=dict)  # input -> {"tags": [...], "accn": ..., "end": ...}
    notes: list[str] = field(default_factory=list)


@dataclass
class Review:
    """Administrator business review (recorded with reviewer and date)."""

    status: str  # "compliant" | "non_compliant"
    reviewer: str
    reviewed_on: date
    notes: str
    prohibited_revenue_pct: float | None = None


def business_screen(inp: Inputs, categories: list[str], tolerance: float | None, review: Review | None) -> dict:
    """status: pass | fail | doubtful, with the activity matched and why."""
    hits_fail, hits_review = [], []
    name = inp.name.lower()
    for c in categories:
        cat = CATEGORIES[c]
        if inp.sic and inp.sic in cat["prohibited"]:
            hits_fail.append(f"{cat['label']} (SIC {inp.sic}{' — ' + inp.sic_description if inp.sic_description else ''})")
        elif inp.sic and inp.sic in cat["review"]:
            hits_review.append(f"{cat['label']} possible (SIC {inp.sic}{' — ' + inp.sic_description if inp.sic_description else ''})")
        else:
            kw = next((k for k in cat["keywords"] if re.search(r"\b" + re.escape(k), name)), None)
            if kw:
                hits_review.append(f"{cat['label']} possible (company name contains '{kw}')")
    base = {"activities_checked": [CATEGORIES[c]["label"] for c in categories], "sic": inp.sic,
            "sic_description": inp.sic_description, "matches": hits_fail + hits_review, "manual_review": None}
    if review:
        base["manual_review"] = review.__dict__ | {"reviewed_on": review.reviewed_on.isoformat()}
        if review.status == "non_compliant":
            return base | {"status": "fail", "explanation": f"Manual review by {review.reviewer} ({review.reviewed_on}): {review.notes}"}
        pct = review.prohibited_revenue_pct
        if pct and (tolerance is None or pct > tolerance):
            why = "this methodology sets no revenue tolerance" if tolerance is None else f"above the {tolerance}% tolerance"
            return base | {"status": "fail", "explanation": f"Manual review: {pct}% of revenue from prohibited activities ({why})"}
        return base | {"status": "pass", "explanation": f"Manual review by {review.reviewer} ({review.reviewed_on}): {review.notes}"}
    if not inp.sic:
        return base | {"status": "doubtful", "explanation": "Industry (SIC code) not available from SEC"}
    if hits_fail:
        return base | {"status": "fail", "explanation": "Prohibited primary business: " + "; ".join(hits_fail)}
    if hits_review:
        return base | {"status": "doubtful", "explanation": "Needs a business review: " + "; ".join(hits_review)}
    return base | {"status": "pass", "explanation": f"No prohibited activity found (SIC {inp.sic}"
                   f"{' — ' + inp.sic_description if inp.sic_description else ''})"}


def _numerator(inp: Inputs, key: str, business: dict, review: Review | None) -> tuple[float | None, str | None]:
    if key == "receivables_plus_cash":
        if inp.receivables is None or inp.cash_ib is None:
            return None, "accounts receivable" if inp.receivables is None else "cash / interest-bearing investments"
        return inp.receivables + inp.cash_ib, None
    if key == "nonpermissible_income":
        if inp.interest_income is None:
            return None, "interest income"
        if review and review.prohibited_revenue_pct is not None:
            if inp.revenue is None:
                return None, "total revenue"
            return inp.interest_income + inp.revenue * review.prohibited_revenue_pct / 100, None
        if business["status"] != "pass":
            return None, "non-compliant activity income (needs a business review)"
        return inp.interest_income, None  # business screen found no prohibited activity
    v = getattr(inp, key)
    return v, None if v is not None else NUMERATORS[key].lower()


def screen(method_key: str, rules: dict, inp: Inputs, as_of: date, review: Review | None = None,
           max_age_days: int = 456) -> dict:
    reasons: list[str] = []
    business = business_screen(inp, rules.get("business") or [], rules.get("business_tolerance_pct"), review)
    failed = business["status"] == "fail"
    doubtful = business["status"] == "doubtful"
    if failed or doubtful:
        reasons.append("Business activity: " + business["explanation"])

    configured = bool(rules.get("source")) and bool(rules.get("tests"))
    if not configured:
        doubtful = True
        reasons.append("Financial thresholds for this methodology are not configured yet — enter them from the "
                       "official methodology document (no thresholds are assumed).")
    if inp.period_end is None:
        doubtful = True
        reasons.append("No balance sheet found in SEC filings")
    elif (as_of - inp.period_end).days > max_age_days:
        doubtful = True
        reasons.append(f"Latest SEC balance sheet ({inp.period_end}) is older than {max_age_days} days")

    ratios = []
    for t in rules.get("tests") or []:
        num, missing = _numerator(inp, t["numerator"], business, review)
        den = getattr(inp, t["denominator"])
        row = {"key": t["key"], "label": t["label"], "numerator": t["numerator"], "numerator_label": NUMERATORS[t["numerator"]],
               "denominator": t["denominator"], "denominator_label": DENOMINATORS[t["denominator"]],
               "op": t["op"], "limit": t["limit"], "numerator_value": num, "denominator_value": den}
        if num is None or den is None or den <= 0:
            what = missing or DENOMINATORS[t["denominator"]].lower()
            row |= {"value": None, "status": "missing"}
            if configured:
                doubtful = True
                reasons.append(f"{t['label']}: {what} not available")
        else:
            v = num / den * 100
            ok = v < t["limit"] if t["op"] == "lt" else v <= t["limit"]
            row |= {"value": round(v, 3), "status": "pass" if ok else "fail"}
            if configured and not ok:
                failed = True
                reasons.append(f"{t['label']} = {v:.2f}% (must be {OPS[t['op']]} {t['limit']}%)")
        ratios.append(row)

    status = NON_COMPLIANT if failed else DOUBTFUL if doubtful else HALAL
    if inp.interest_income_note:
        reasons.append("Note: " + inp.interest_income_note)
    return {"methodology": method_key, "methodology_label": rules["label"], "rules_source": rules.get("source"),
            "status": status, "status_label": LABELS[status], "screened_on": as_of.isoformat(),
            "business": business, "ratios": ratios, "reasons": reasons,
            "financials_period_end": inp.period_end.isoformat() if inp.period_end else None,
            "inputs": {k: getattr(inp, k) for k in ("total_assets", "debt", "cash_ib", "receivables", "revenue",
                                                     "interest_income", "market_cap", "avg_market_cap_12m",
                                                     "avg_market_cap_24m", "avg_market_cap_36m")},
            "sources": inp.sources, "notes": inp.notes, "disclaimer": DISCLAIMER}


def inputs_from_fundamentals(f) -> Inputs:
    """Engine inputs from a per-company SEC companyfacts read (app.screening.shariah.Fundamentals)."""
    return Inputs(ticker=f.ticker, name=f.company_name, sic=f.sic, period_end=f.period_end, total_assets=f.total_assets,
                  debt=f.debt, cash_ib=f.cash_and_securities, receivables=f.receivables, revenue=f.revenue,
                  interest_income=f.interest_income,
                  interest_income_note="interest income figure includes dividends (conservative)"
                  if f.interest_income_includes_dividends else None,
                  market_cap=f.market_cap, avg_market_cap_24m=f.avg_market_cap_24m, avg_market_cap_36m=f.avg_market_cap_36m,
                  sources={"companyfacts": f.raw}, notes=[f.source])
