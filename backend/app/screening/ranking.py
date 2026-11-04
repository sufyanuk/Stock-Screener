"""Eligibility and ranking.

Eligibility (all must hold for the main Top 5):
  * Mandatory, non-configurable: price < $100 (sourced, not missing).
  * Every criterion marked ``hard`` must PASS. MISSING never passes.

Shariah status is **not** an eligibility condition. It is computed for display only: the UI
highlights compliant tickers in green and non-compliant ones in red.

Overall score (0–100) = Σ weight_i × subscore_i / 100 over the six factors.

Watchlist: stocks under $100 that fail between 1 and ``watchlist_max_failures`` hard criteria,
ranked by score, each with its failures listed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import ScreeningConfig
from app.screening.types import CriterionResult

FACTOR_WEIGHT_KEY = {"options": "options", "insider": "insider", "analyst": "analyst",
                     "technical": "technical", "float": "float_", "liquidity": "liquidity"}


@dataclass
class Candidate:
    ticker: str
    company: str
    price: float | None
    day_change_pct: float | None
    shariah_status: str  # display only (highlight colour)
    criteria: dict[str, CriterionResult]
    extra: dict = field(default_factory=dict)

    score: float = 0.0
    eligible: bool = False
    failures: list[str] = field(default_factory=list)

    def compute(self, cfg: ScreeningConfig) -> "Candidate":
        w = cfg.weights
        self.score = round(sum(getattr(w, FACTOR_WEIGHT_KEY[k]) * c.score / 100 for k, c in self.criteria.items()
                               if k in FACTOR_WEIGHT_KEY), 2)
        f = []
        if self.price is None:
            f.append("price: missing")
        elif not self.price < cfg.max_price:
            f.append(f"price: ${self.price:.2f} ≥ ${cfg.max_price:.0f}")
        for k, c in self.criteria.items():
            if c.hard and not c.passed:
                f.append(f"{k}: {c.outcome.value}")
        self.failures = f
        self.eligible = not f
        return self


def rank(cands: list[Candidate], cfg: ScreeningConfig) -> tuple[list[Candidate], list[Candidate]]:
    for c in cands:
        c.compute(cfg)
    order = lambda c: (-c.score, c.ticker)  # deterministic tie-break
    top = sorted([c for c in cands if c.eligible], key=order)[: cfg.top_n]
    watch = [c for c in cands if not c.eligible and c.price is not None and c.price < cfg.max_price
             and 1 <= len(c.failures) <= cfg.watchlist_max_failures]
    return top, sorted(watch, key=order)[:25]
