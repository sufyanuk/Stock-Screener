"""Low-float and liquidity screening.

Float Ratio = Public Float / Total Shares Outstanding × 100.

Liquidity/manipulation flags: low average dollar volume, wide bid/ask spread, recent trading
halt, very low float combined with an extreme one-day move (possible pump-and-dump).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import FloatCriteria, LiquidityCriteria
from app.screening.types import CriterionResult, Outcome, lerp_score


@dataclass
class FloatInput:
    shares_outstanding: float | None
    float_shares: float | None
    institutional_ownership_pct: float | None = None
    insider_ownership_pct: float | None = None
    short_interest_shares: float | None = None
    days_to_cover: float | None = None
    avg_daily_volume: float | None = None


def float_category(ratio: float, cfg: FloatCriteria) -> str:
    if ratio < cfg.very_low_pct:
        return "Very Low Float"
    if ratio < cfg.low_pct:
        return "Low Float"
    if ratio <= cfg.moderate_pct:
        return "Moderate Float"
    return "High Float"


CATEGORY_SCORE = {"Very Low Float": 100, "Low Float": 75, "Moderate Float": 40, "High Float": 10}


def evaluate_float(inp: FloatInput, cfg: FloatCriteria) -> CriterionResult:
    m = {"shares_outstanding": inp.shares_outstanding, "float_shares": inp.float_shares,
         "institutional_ownership_pct": inp.institutional_ownership_pct,
         "insider_ownership_pct": inp.insider_ownership_pct, "days_to_cover": inp.days_to_cover,
         "avg_daily_volume": inp.avg_daily_volume, "short_interest_shares": inp.short_interest_shares}
    if not inp.shares_outstanding or inp.float_shares is None:
        return CriterionResult("float", Outcome.MISSING, cfg.hard, 0.0, "Float or shares outstanding unavailable.", m)
    ratio = inp.float_shares / inp.shares_outstanding * 100
    cat = float_category(ratio, cfg)
    m.update({"float_ratio_pct": ratio, "category": cat,
              "short_pct_float": (inp.short_interest_shares / inp.float_shares * 100)
              if inp.short_interest_shares is not None and inp.float_shares else None})
    ok = ratio <= cfg.moderate_pct
    return CriterionResult("float", Outcome.PASS if ok else Outcome.FAIL, cfg.hard, CATEGORY_SCORE[cat],
                           f"Float ratio {ratio:.1f}% → {cat}. Low float raises ranking but amplifies volatility and liquidity risk.", m)


@dataclass
class LiquidityInput:
    avg_dollar_volume: float | None
    relative_volume: float | None
    bid: float | None
    ask: float | None
    halted_recently: bool | None
    day_change_pct: float | None
    float_ratio_pct: float | None


def evaluate_liquidity(inp: LiquidityInput, cfg: LiquidityCriteria) -> CriterionResult:
    flags = []
    spread = None
    if inp.bid and inp.ask and inp.ask > 0:
        spread = (inp.ask - inp.bid) / ((inp.ask + inp.bid) / 2) * 100
        if spread > cfg.max_spread_pct:
            flags.append(f"wide spread {spread:.2f}%")
    if inp.avg_dollar_volume is not None and inp.avg_dollar_volume < cfg.min_avg_dollar_volume:
        flags.append(f"low dollar volume ${inp.avg_dollar_volume:,.0f}/day")
    if inp.halted_recently:
        flags.append("recent trading halt")
    if inp.float_ratio_pct is not None and inp.float_ratio_pct < 10 and inp.day_change_pct is not None and abs(inp.day_change_pct) > 25:
        flags.append("very low float with >25% daily move (possible pump-and-dump)")
    m = {"avg_dollar_volume": inp.avg_dollar_volume, "relative_volume": inp.relative_volume,
         "spread_pct": spread, "halted_recently": inp.halted_recently, "flags": flags}
    if inp.avg_dollar_volume is None or inp.relative_volume is None:
        return CriterionResult("liquidity", Outcome.MISSING, cfg.hard, 0.0, "Volume data unavailable.", m)
    score = 0.6 * lerp_score(inp.relative_volume, 0.5, 3.0) + 0.4 * lerp_score(inp.avg_dollar_volume, cfg.min_avg_dollar_volume, 50e6)
    score = max(0.0, score - 25 * len(flags))
    ok = not flags
    expl = f"Relative volume {inp.relative_volume:.2f}×, avg dollar volume ${inp.avg_dollar_volume:,.0f}."
    expl += (" Flags: " + "; ".join(flags) + ".") if flags else " No liquidity red flags."
    if inp.halted_recently is None:
        expl += " Halt status unknown."
    return CriterionResult("liquidity", Outcome.PASS if ok else Outcome.FAIL, cfg.hard, score, expl, m)
