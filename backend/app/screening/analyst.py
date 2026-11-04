"""Analyst ratings and sentiment.

Consensus score = weighted mean of rating counts on a 1..5 scale
(Strong Sell=1, Sell=2, Hold=3, Buy=4, Strong Buy=5).

Analyst-accuracy weighting is applied **only** when an individual rating carries an
independently sourced historical success rate (``verified_success_rate`` + ``accuracy_source``).
Otherwise every rating gets weight 1 and the result is flagged ``accuracy_weighting=false``.
No analyst is ever labelled "top performing" without such a source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from app.config import AnalystCriteria
from app.screening.types import CriterionResult, Outcome, clamp, lerp_score

RATING_VALUE = {"strong_buy": 5, "buy": 4, "hold": 3, "sell": 2, "strong_sell": 1}


@dataclass
class RatingAction:
    date: date
    firm: str
    analyst: str | None
    action: str  # upgrade | downgrade | maintain | initiate | reiterate
    new_grade: str
    previous_grade: str | None = None
    price_target: float | None = None
    verified_success_rate: float | None = None  # 0..1, only from a documented source
    accuracy_source: str | None = None


@dataclass
class AnalystInput:
    ticker: str
    as_of: date
    price: float | None
    counts: dict[str, int] | None  # keys of RATING_VALUE
    target_avg: float | None
    target_high: float | None
    target_low: float | None
    target_median: float | None = None
    actions: list[RatingAction] = field(default_factory=list)
    institutional_change_pct: float | None = None


def consensus_label(score: float) -> str:
    if score >= 4.5:
        return "Strong Buy"
    if score >= 3.5:
        return "Buy"
    if score >= 2.5:
        return "Hold"
    if score >= 1.5:
        return "Sell"
    return "Strong Sell"


def accuracy_weight(a: RatingAction) -> float:
    if a.verified_success_rate is None or not a.accuracy_source:
        return 1.0
    return clamp(1.0 + (a.verified_success_rate - 0.5) * 2.0, 0.25, 2.0)


def evaluate_analyst(inp: AnalystInput, cfg: AnalystCriteria) -> CriterionResult:
    counts = inp.counts or {}
    n = sum(counts.get(k, 0) for k in RATING_VALUE)
    metrics: dict = {"counts": counts, "coverage": n, "target_avg": inp.target_avg, "target_high": inp.target_high,
                     "target_low": inp.target_low, "target_median": inp.target_median,
                     "institutional_change_pct": inp.institutional_change_pct}
    if n < cfg.min_coverage:
        return CriterionResult("analyst", Outcome.MISSING, cfg.hard, 0.0,
                               f"Analyst coverage {n} < minimum {cfg.min_coverage}.", metrics)
    score_1_5 = sum(RATING_VALUE[k] * counts.get(k, 0) for k in RATING_VALUE) / n

    upside = None
    if inp.target_avg and inp.price:
        upside = (inp.target_avg / inp.price - 1) * 100

    def window(days: int) -> dict:
        start = inp.as_of - timedelta(days=days)
        acts = [a for a in inp.actions if start <= a.date <= inp.as_of]
        up = sum(accuracy_weight(a) for a in acts if a.action == "upgrade")
        down = sum(accuracy_weight(a) for a in acts if a.action == "downgrade")
        return {"actions": len(acts), "upgrades_weighted": up, "downgrades_weighted": down,
                "upgrades": sum(a.action == "upgrade" for a in acts),
                "downgrades": sum(a.action == "downgrade" for a in acts)}

    w30, w90 = window(30), window(90)
    weighted = any(a.verified_success_rate is not None and a.accuracy_source for a in inp.actions)
    metrics.update({
        "consensus_score": round(score_1_5, 3), "consensus": consensus_label(score_1_5), "upside_pct": upside,
        "changes_30d": w30, "changes_90d": w90, "accuracy_weighting": weighted,
        "recent": [{"date": a.date.isoformat(), "firm": a.firm, "analyst": a.analyst, "action": a.action,
                    "from": a.previous_grade, "to": a.new_grade, "target": a.price_target,
                    "success_rate": a.verified_success_rate, "accuracy_source": a.accuracy_source}
                   for a in sorted(inp.actions, key=lambda a: a.date, reverse=True)[:15]],
    })
    net = w90["upgrades_weighted"] - w90["downgrades_weighted"]
    score = (0.5 * lerp_score(score_1_5, 2.5, 5.0)
             + 0.3 * (lerp_score(upside, 0, 50) if upside is not None else 0)
             + 0.2 * lerp_score(net, -3, 3))
    ok = score_1_5 >= 3.5 and (upside is None or upside > 0)
    expl = (f"Consensus {consensus_label(score_1_5)} ({score_1_5:.2f}/5 from {n} analysts)"
            + (f", average target implies {upside:+.1f}% upside" if upside is not None else ", no price target")
            + f"; 90-day upgrades/downgrades {w90['upgrades']}/{w90['downgrades']}."
            + ("" if weighted else " Ratings are equally weighted (no verified analyst track records available)."))
    return CriterionResult("analyst", Outcome.PASS if ok else Outcome.FAIL, cfg.hard, score, expl, metrics)
