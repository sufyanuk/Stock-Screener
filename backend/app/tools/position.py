"""Position-size calculator (long and short).

Risk Amount      = Account Balance × Risk %
Risk Per Share   = |Entry − Stop| + per-share slippage (entry and exit) + per-share commission
Risk-based size  = (Risk Amount − fixed commissions) / Risk Per Share
Allocation size  = min(Max Allocation % × Balance, Available Capital) / (Entry + entry slippage)
Shares           = min(risk-based, allocation-based), floored unless fractional shares allowed
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class PositionInput(BaseModel):
    account_balance: float = Field(gt=0)
    risk_pct: float = Field(gt=0, le=100)
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    target1: float | None = Field(default=None, gt=0)
    target2: float | None = Field(default=None, gt=0)
    max_allocation_pct: float = Field(default=100, gt=0, le=100)
    available_capital: float | None = Field(default=None, ge=0)
    commission_per_trade: float = Field(default=0, ge=0)  # charged on entry and on exit
    commission_per_share: float = Field(default=0, ge=0)
    slippage_per_share: float = Field(default=0, ge=0)  # applied adversely on entry and exit
    direction: Literal["long", "short"] = "long"
    fractional: bool = False

    @model_validator(mode="after")
    def _check(self) -> "PositionInput":
        if self.entry == self.stop:
            raise ValueError("entry and stop must differ")
        if self.direction == "long" and self.stop > self.entry:
            raise ValueError("long stop must be below entry")
        if self.direction == "short" and self.stop < self.entry:
            raise ValueError("short stop must be above entry")
        return self


def calculate_position(p: PositionInput) -> dict:
    sign = 1 if p.direction == "long" else -1
    risk_amount = p.account_balance * p.risk_pct / 100
    fill_entry = p.entry + sign * p.slippage_per_share
    fill_stop = p.stop - sign * p.slippage_per_share
    risk_per_share = abs(fill_entry - fill_stop) + 2 * p.commission_per_share
    fixed = 2 * p.commission_per_trade
    risk_shares = max(0.0, (risk_amount - fixed) / risk_per_share)
    capital = p.account_balance if p.available_capital is None else min(p.available_capital, p.account_balance)
    alloc_cap = min(p.account_balance * p.max_allocation_pct / 100, capital)
    alloc_shares = max(0.0, (alloc_cap - p.commission_per_trade) / (fill_entry + p.commission_per_share))
    raw = min(risk_shares, alloc_shares)
    shares = raw if p.fractional else math.floor(raw)
    shares = round(shares, 6)
    limited_by = "risk" if risk_shares <= alloc_shares else "allocation"

    investment = shares * fill_entry
    entry_costs = p.commission_per_trade + shares * p.commission_per_share
    capital_required = investment + entry_costs
    max_loss = shares * risk_per_share + fixed if shares else 0.0

    def profit(target: float | None) -> float | None:
        if target is None:
            return None
        fill_t = target - sign * p.slippage_per_share
        return round(shares * (sign * (fill_t - fill_entry) - 2 * p.commission_per_share) - fixed, 2)

    p1, p2 = profit(p.target1), profit(p.target2)
    return {
        "direction": p.direction, "risk_amount": round(risk_amount, 2), "risk_per_share": round(risk_per_share, 4),
        "risk_based_shares": round(risk_shares, 4), "allocation_based_shares": round(alloc_shares, 4),
        "shares": shares, "limited_by": limited_by,
        "effective_entry": round(fill_entry, 4), "effective_stop": round(fill_stop, 4),
        "investment": round(investment, 2), "capital_required": round(capital_required, 2),
        "max_loss": round(max_loss, 2), "profit_target1": p1, "profit_target2": p2,
        "rr_target1": round(p1 / max_loss, 2) if p1 is not None and max_loss else None,
        "rr_target2": round(p2 / max_loss, 2) if p2 is not None and max_loss else None,
        "pct_of_account": round(capital_required / p.account_balance * 100, 2),
        "remaining_cash": round(capital - capital_required, 2),
    }
