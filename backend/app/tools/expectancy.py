"""Trade expectancy, profit factor, risk of ruin and drawdown estimates.

Expectancy per trade = W × AvgWin − (1 − W) × AvgLoss − costs
Expectancy (R)       = Expectancy / Risk per trade
Profit factor        = (W × AvgWin) / ((1 − W) × AvgLoss)
Break-even win rate  = (AvgLoss + costs) / (AvgWin + AvgLoss)

Risk of ruin is estimated by Monte Carlo simulation (fixed-dollar risk, i.i.d. trades with the
stated win rate and average win/loss, ruin = equity falls to ≤ ruin_threshold × starting
capital). Assumptions are returned with the result. Positive historical expectancy does
**not** guarantee future profits.
"""
from __future__ import annotations

import numpy as np
from pydantic import BaseModel, Field


class ExpectancyInput(BaseModel):
    win_rate_pct: float = Field(ge=0, le=100)
    avg_win: float = Field(ge=0)
    avg_loss: float = Field(ge=0)  # positive number
    num_trades: int = Field(default=100, ge=1, le=100_000)
    risk_per_trade: float = Field(default=0, ge=0)
    cost_per_trade: float = Field(default=0, ge=0)
    starting_capital: float | None = Field(default=None, gt=0)
    ruin_threshold_pct: float = Field(default=50, gt=0, lt=100)  # ruin = losing this % of capital
    simulations: int = Field(default=5000, ge=100, le=50_000)
    seed: int | None = 42
    historical_returns: list[float] | None = None  # optional actual trade P&L sequence


def max_drawdown(equity: np.ndarray) -> float:
    peaks = np.maximum.accumulate(equity)
    return float((peaks - equity).max()) if len(equity) else 0.0


def calculate_expectancy(p: ExpectancyInput) -> dict:
    w = p.win_rate_pct / 100
    e = w * p.avg_win - (1 - w) * p.avg_loss - p.cost_per_trade
    gross_loss = (1 - w) * p.avg_loss
    pf = (w * p.avg_win) / gross_loss if gross_loss > 0 else None
    be = (p.avg_loss + p.cost_per_trade) / (p.avg_win + p.avg_loss) * 100 if (p.avg_win + p.avg_loss) else None
    out = {
        "expectancy_per_trade": round(e, 4), "expected_total": round(e * p.num_trades, 2),
        "expectancy_r": round(e / p.risk_per_trade, 4) if p.risk_per_trade else None,
        "profit_factor": round(pf, 4) if pf is not None else None,
        "break_even_win_rate_pct": round(be, 2) if be is not None else None,
        "payoff_ratio": round(p.avg_win / p.avg_loss, 4) if p.avg_loss else None,
        "disclaimer": "Expectancy is a statistical estimate from the inputs; it does not guarantee future results.",
    }
    rng = np.random.default_rng(p.seed)
    wins = rng.random((p.simulations, p.num_trades)) < w
    pnl = np.where(wins, p.avg_win, -p.avg_loss) - p.cost_per_trade
    eq = np.cumsum(pnl, axis=1)
    dd = np.maximum.accumulate(np.concatenate([np.zeros((p.simulations, 1)), eq], axis=1), axis=1)[:, 1:] - eq
    mdd = dd.max(axis=1)
    out["simulated_max_drawdown"] = {"median": round(float(np.median(mdd)), 2), "p95": round(float(np.percentile(mdd, 95)), 2)}
    if p.starting_capital:
        ruin_level = -p.starting_capital * p.ruin_threshold_pct / 100
        ruined = (eq <= ruin_level).any(axis=1)
        out["risk_of_ruin_pct"] = round(float(ruined.mean() * 100), 2)
    out["assumptions"] = {
        "method": "Monte Carlo", "simulations": p.simulations, "trades_per_path": p.num_trades,
        "position_sizing": "fixed dollar win/loss per trade (no compounding)", "independence": "trades i.i.d.",
        "ruin_definition": f"equity drawdown ≥ {p.ruin_threshold_pct}% of starting capital" if p.starting_capital else None,
    }
    pct = np.percentile(eq, [5, 50, 95], axis=0)
    step = max(1, p.num_trades // 100)
    out["equity_bands"] = [{"trade": i + 1, "p5": round(float(pct[0, i]), 2), "p50": round(float(pct[1, i]), 2),
                            "p95": round(float(pct[2, i]), 2)} for i in range(0, p.num_trades, step)]
    if p.historical_returns:
        h = np.array(p.historical_returns, dtype=float)
        out["historical_max_drawdown"] = round(max_drawdown(np.concatenate([[0], np.cumsum(h)])), 2)
    return out


def scenario_grid(avg_loss: float, win_rates=(30, 40, 50, 60, 70), rr=(1, 1.5, 2, 3, 4), cost: float = 0) -> list[dict]:
    """Expectancy per trade for a grid of win rates × reward/risk ratios (avg_win = rr × avg_loss)."""
    return [{"win_rate_pct": wr, "rr": r,
             "expectancy": round(wr / 100 * r * avg_loss - (1 - wr / 100) * avg_loss - cost, 2)}
            for wr in win_rates for r in rr]
