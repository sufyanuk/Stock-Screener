"""Insider buying from SEC Form 4.

Only *open-market purchases* qualify: non-derivative transactions with transaction code ``P``
and acquired/disposed code ``A``. Everything else (``A`` grants/awards, ``M``/``X`` option
exercises, ``G`` gifts, ``F`` tax withholding, ``J`` other, ``C`` conversions, ``S`` sales…) is
excluded and listed with the reason.

De-duplication:
  1. Exact duplicates: same accession number + transaction index (amended re-ingestion).
  2. Amendments (4/A) supersede the original for the same reporting owner/date/shares.
  3. Joint / indirect reporting: several reporting owners (e.g. a trust and its trustee) often
     report the *same* economic purchase. Rows with the same issuer, trade date, share count
     and price but different reporting owners are collapsed to one economic transaction and
     flagged ``joint_report``. Aggregates count each economic transaction once.

% of outstanding = qualifying shares / shares outstanding, where shares outstanding is the
latest ``dei:EntityCommonStockSharesOutstanding`` value reported *on or before* the trade date
(point-in-time).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable

from app.config import InsiderCriteria
from app.screening.types import CriterionResult, Outcome, lerp_score

EXCLUDED_CODES = {
    "A": "grant/award", "M": "option exercise", "X": "option exercise", "G": "gift",
    "F": "tax withholding", "J": "other acquisition", "C": "conversion", "S": "sale",
    "D": "disposition to issuer", "I": "discretionary transaction", "W": "will/inheritance",
    "Z": "trust deposit", "L": "small acquisition", "K": "equity swap", "E": "expiration",
    "H": "expiration", "O": "out-of-money exercise", "U": "tender",
}

SENIOR_KEYWORDS = ("chief executive", "ceo", "chief financial", "cfo", "president", "chair", "coo", "chief operating")


@dataclass
class InsiderTxn:
    accession: str
    txn_index: int
    issuer_cik: str
    ticker: str
    owner_name: str
    owner_cik: str
    officer_title: str
    is_director: bool
    is_officer: bool
    is_ten_pct_owner: bool
    transaction_date: date
    filing_date: date
    code: str
    acquired_disposed: str
    shares: float
    price: float | None
    shares_owned_after: float | None
    direct_indirect: str  # "D" | "I"
    security_title: str = ""
    is_amendment: bool = False
    is_derivative: bool = False
    footnotes: str = ""

    @property
    def designation(self) -> str:
        roles = []
        if self.officer_title:
            roles.append(self.officer_title)
        elif self.is_officer:
            roles.append("Officer")
        if self.is_director:
            roles.append("Director")
        if self.is_ten_pct_owner:
            roles.append("10% Owner")
        return ", ".join(roles) or "Unknown"

    @property
    def is_senior(self) -> bool:
        t = self.officer_title.lower()
        return any(k in t for k in SENIOR_KEYWORDS)

    @property
    def value(self) -> float | None:
        return None if self.price is None else self.shares * self.price


def qualification(t: InsiderTxn) -> tuple[bool, str]:
    if t.is_derivative:
        return False, "derivative security (not common stock purchase)"
    if t.code != "P":
        return False, EXCLUDED_CODES.get(t.code, f"code {t.code}")
    if t.acquired_disposed != "A":
        return False, "not an acquisition"
    if t.price is None or t.price <= 0:
        return False, "no purchase price reported (not a funded open-market buy)"
    return True, "open-market purchase"


@dataclass
class EconomicPurchase:
    """One economic open-market purchase (possibly reported by several owners)."""

    rows: list[InsiderTxn]
    joint_report: bool = False

    @property
    def primary(self) -> InsiderTxn:
        # Prefer a natural person / direct holder as the displayed owner
        return sorted(self.rows, key=lambda r: (r.direct_indirect != "D", r.owner_name))[0]

    @property
    def shares(self) -> float:
        return self.primary.shares


def dedupe_purchases(txns: Iterable[InsiderTxn]) -> tuple[list[EconomicPurchase], list[dict]]:
    excluded: list[dict] = []
    by_key: dict[tuple, InsiderTxn] = {}
    for t in txns:
        ok, why = qualification(t)
        if not ok:
            excluded.append({"owner": t.owner_name, "date": t.transaction_date.isoformat(), "code": t.code,
                             "shares": t.shares, "reason": why, "accession": t.accession})
            continue
        by_key.setdefault((t.accession, t.txn_index, t.owner_cik), t)  # exact duplicate re-ingestion

    # Amendments (4/A) supersede original filings of the same owner/date/size/price
    lots: dict[tuple, list[InsiderTxn]] = {}
    for t in by_key.values():
        lots.setdefault((t.owner_cik, t.transaction_date, t.shares, t.price), []).append(t)
    unique: list[InsiderTxn] = []
    for rows in lots.values():
        amended = [r for r in rows if r.is_amendment]
        unique.extend(amended if amended else rows)

    groups: dict[tuple, list[InsiderTxn]] = {}
    for t in unique:
        gkey = (t.issuer_cik, t.transaction_date, round(t.shares, 4), round(t.price or 0, 4))
        groups.setdefault(gkey, []).append(t)
    out = []
    for rows in groups.values():
        owners = {r.owner_cik for r in rows}
        if len(owners) > 1:
            out.append(EconomicPurchase(rows, joint_report=True))
        else:
            # Same owner, same day/size/price in different filings = distinct lots only if
            # different accession *and* different txn index; keep each separately.
            out.extend(EconomicPurchase([r]) for r in rows)
    out.sort(key=lambda p: p.primary.transaction_date, reverse=True)
    return out, excluded


@dataclass
class InsiderInput:
    ticker: str
    as_of: date
    transactions: list[InsiderTxn]
    shares_outstanding: float | None
    shares_outstanding_date: date | None
    extra: dict = field(default_factory=dict)


def evaluate_insider(inp: InsiderInput, cfg: InsiderCriteria) -> CriterionResult:
    start = inp.as_of - timedelta(days=cfg.lookback_days)
    window = [t for t in inp.transactions if start <= t.transaction_date <= inp.as_of and t.filing_date <= inp.as_of]
    purchases, excluded = dedupe_purchases(window)
    so = inp.shares_outstanding

    rows = []
    for p in purchases:
        t = p.primary
        before = None if t.shares_owned_after is None else t.shares_owned_after - t.shares
        rows.append({
            "owner": t.owner_name, "designation": t.designation, "senior": t.is_senior,
            "transaction_date": t.transaction_date.isoformat(), "filing_date": t.filing_date.isoformat(),
            "shares": t.shares, "price": t.price, "value": t.value,
            "pct_outstanding": (t.shares / so * 100) if so else None,
            "owned_before": before, "owned_after": t.shares_owned_after,
            "joint_report": p.joint_report, "co_reporters": [r.owner_name for r in p.rows if r is not t],
            "accession": t.accession,
        })

    total_shares = sum(p.shares for p in purchases)
    max_single = max((p.shares for p in purchases), default=0.0)
    metrics = {
        "lookback_days": cfg.lookback_days, "basis": cfg.basis, "purchases": rows, "excluded": excluded[:50],
        "excluded_count": len(excluded), "unique_purchase_count": len(purchases),
        "distinct_insiders": len({p.primary.owner_cik for p in purchases}),
        "aggregate_shares": total_shares, "largest_single_shares": max_single,
        "shares_outstanding": so,
        "shares_outstanding_date": inp.shares_outstanding_date.isoformat() if inp.shares_outstanding_date else None,
        "aggregate_pct": (total_shares / so * 100) if so else None,
        "largest_single_pct": (max_single / so * 100) if so else None,
    }
    if not so:
        return CriterionResult("insider", Outcome.MISSING, cfg.hard, 0.0,
                               "Shares outstanding unavailable — insider % cannot be computed.", metrics)
    if not purchases:
        return CriterionResult("insider", Outcome.FAIL, cfg.hard, 0.0,
                               f"No qualifying open-market purchases in the last {cfg.lookback_days} days "
                               f"({len(excluded)} non-qualifying transactions excluded).", metrics)
    pct = metrics["aggregate_pct"] if cfg.basis == "aggregate_unique" else metrics["largest_single_pct"]
    ok = pct > cfg.min_pct_outstanding
    senior = any(r["senior"] for r in rows)
    score = 0.7 * lerp_score(pct, 0, 5) + 0.15 * (100 if senior else 0) + 0.15 * lerp_score(metrics["distinct_insiders"], 1, 4)
    basis_txt = "aggregate of unique purchases by all insiders" if cfg.basis == "aggregate_unique" else "largest single purchase"
    expl = (f"{'Passes' if ok else 'Fails'}: {basis_txt} = {pct:.2f}% of shares outstanding "
            f"(need >{cfg.min_pct_outstanding:.2f}%). {len(purchases)} unique purchase(s) by "
            f"{metrics['distinct_insiders']} insider(s){' incl. a senior executive' if senior else ''}.")
    if any(p.joint_report for p in purchases):
        expl += " Joint/indirect filings of the same purchase were counted once."
    return CriterionResult("insider", Outcome.PASS if ok else Outcome.FAIL, cfg.hard, score, expl, metrics)
