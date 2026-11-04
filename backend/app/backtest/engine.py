"""Historical backtesting of the screening methodology.

Look-ahead protection (enforced in code, not by convention):
  * Options history: only sessions ≤ D from ``options_daily``.
  * Insider filings: ``filing_date ≤ D`` (a purchase is only known once filed).
  * Fundamentals / Shariah: XBRL facts with ``filed ≤ D``; business reviews with ``reviewed_on ≤ D``.
  * Prices: bars ≤ D for signals; entries happen at the **next** session's open.
  * Analyst consensus and float are only available as *current* snapshots from the providers,
    so the point-in-time wrapper returns them as unavailable (scored 0, flagged) instead of
    leaking today's values into the past.

Survivorship bias: the universe includes inactive (delisted) securities that were ever ingested.
The SEC ticker list only contains current registrants, so history ingested before this system
went live is still survivorship-biased; the report states this.

Execution model: enter at next open + slippage; exit at the stop (low ≤ stop), Target 1
(high ≥ T1), or at the close after ``max_hold_days``. When stop and target fall in the same
bar, the stop is assumed (conservative). Commission is charged on entry and exit.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from app import market_calendar as mc
from app.screening.types import Freshness, Provenance, now_utc
from app.services.datasource import AnalystData, DataSource


class BacktestParams(BaseModel):
    start: date
    end: date
    top_n: int = Field(5, ge=1, le=5)
    max_hold_days: int = Field(20, ge=1, le=250)
    slippage_bps: float = Field(10, ge=0)
    commission_per_trade: float = Field(1.0, ge=0)
    position_value: float = Field(10_000, gt=0)
    regime_ticker: str = "SPY"


class PointInTimeDataSource:
    """Wraps a live source and blocks values that are not available point-in-time."""

    def __init__(self, inner: DataSource):
        self.inner = inner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    # Current consensus / float would leak future information, so they are not requested at all.
    @staticmethod
    def _blocked(name: str) -> Provenance:
        return Provenance(f"{name} (not point-in-time; excluded)", Freshness.DERIVED, now_utc())

    async def analyst(self, ticker):
        return AnalystData(None, None, []), self._blocked("analyst")

    async def float_info(self, ticker):
        return {}, self._blocked("float")

    async def quote(self, ticker):  # pragma: no cover - live=False in backtests
        raise RuntimeError("live quotes are not allowed in backtests")


@dataclass
class SimTrade:
    ticker: str
    signal_date: date
    entry_date: date
    entry: float
    stop: float
    target: float | None
    exit_date: date
    exit: float
    reason: str
    pnl: float
    ret_pct: float
    r_multiple: float
    regime: str
    score: float


def simulate_trade(bars: pd.DataFrame, signal_date: date, stop: float, target: float | None, p: BacktestParams):
    after = bars[bars.index > pd.Timestamp(signal_date)]
    if after.empty:
        return None
    slip = p.slippage_bps / 10_000
    entry_bar = after.iloc[0]
    entry = float(entry_bar["open"]) * (1 + slip)
    if stop >= entry:
        return None  # gapped through the stop: no valid entry
    shares = (p.position_value - p.commission_per_trade) / entry
    for i, (ts, b) in enumerate(after.iloc[: p.max_hold_days].iterrows()):
        if b["low"] <= stop:
            px = min(stop, float(b["open"])) * (1 - slip)
            return entry, ts.date(), px, "stop", shares, after.index[0].date()
        if target and b["high"] >= target:
            px = max(target, float(b["open"])) * (1 - slip) if i else target * (1 - slip)
            return entry, ts.date(), px, "target1", shares, after.index[0].date()
    last = after.iloc[min(p.max_hold_days, len(after)) - 1]
    return entry, after.index[min(p.max_hold_days, len(after)) - 1].date(), float(last["close"]) * (1 - slip), "time", shares, after.index[0].date()


def metrics(trades: list[SimTrade], p: BacktestParams) -> dict:
    if not trades:
        return {"trades": 0}
    pnl = np.array([t.pnl for t in trades])
    rets = np.array([t.ret_pct for t in trades])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    eq = np.cumsum(pnl)
    dd = eq - np.maximum.accumulate(np.concatenate([[0], eq]))[1:]
    hold = np.mean([(t.exit_date - t.entry_date).days or 1 for t in trades])
    sharpe = None
    if len(rets) > 2 and rets.std(ddof=1) > 0:
        sharpe = float(rets.mean() / rets.std(ddof=1) * np.sqrt(252 / max(hold, 1)))
    by_regime = {}
    for reg in sorted({t.regime for t in trades}):
        sub = [t for t in trades if t.regime == reg]
        by_regime[reg] = {"trades": len(sub), "win_rate_pct": round(100 * sum(t.pnl > 0 for t in sub) / len(sub), 2),
                          "avg_return_pct": round(float(np.mean([t.ret_pct for t in sub])), 3)}
    return {
        "trades": len(trades), "win_rate_pct": round(100 * len(wins) / len(trades), 2),
        "avg_return_pct": round(float(rets.mean()), 3),
        "avg_win": round(float(wins.mean()), 2) if len(wins) else None,
        "avg_loss": round(float(losses.mean()), 2) if len(losses) else None,
        "profit_factor": round(float(wins.sum() / -losses.sum()), 3) if len(losses) and losses.sum() < 0 else None,
        "expectancy": round(float(pnl.mean()), 2), "expectancy_r": round(float(np.mean([t.r_multiple for t in trades])), 3),
        "max_drawdown": round(float(dd.min()), 2), "total_pnl": round(float(pnl.sum()), 2),
        "sharpe_trade_based": round(sharpe, 3) if sharpe is not None else None,
        "sharpe_note": "per-trade returns annualised by average holding period; approximate",
        "by_regime": by_regime,
        "equity_curve": [{"date": t.exit_date.isoformat(), "equity": round(float(e), 2)} for t, e in zip(trades, eq)],
    }


async def run_backtest(engine, p: BacktestParams) -> dict:
    """``engine``: a ScanEngine whose datasource is wrapped in PointInTimeDataSource."""
    engine.persist = False
    sessions = [d for d in mc.previous_sessions(p.end, 2000) if d >= p.start]
    trades: list[SimTrade] = []
    signals = 0
    reg_bars = await engine.load_bars(p.regime_ticker, p.end)
    for d in sessions:
        cands, top, _, _ = await engine.screen(d, live=False, include_inactive=True)
        for c in top[: p.top_n]:
            plan = c.extra.get("trade_plan") or {}
            if not plan.get("stop_loss"):
                continue
            signals += 1
            bars = await engine.load_bars(c.ticker, p.end)
            if bars is None:
                continue
            sim = simulate_trade(bars, d, plan["stop_loss"], plan.get("target1"), p)
            if not sim:
                continue
            entry, exit_d, exit_px, reason, shares, entry_d = sim
            pnl = shares * (exit_px - entry) - 2 * p.commission_per_trade
            regime = "unknown"
            if reg_bars is not None:
                rb = reg_bars[reg_bars.index <= pd.Timestamp(d)]["close"]
                if len(rb) >= 200:
                    regime = "bull (SPY > 200d SMA)" if rb.iloc[-1] > rb.tail(200).mean() else "bear (SPY < 200d SMA)"
            risk = entry - plan["stop_loss"]
            trades.append(SimTrade(c.ticker, d, entry_d, entry, plan["stop_loss"], plan.get("target1"), exit_d, exit_px,
                                   reason, pnl, pnl / p.position_value * 100,
                                   (exit_px - entry) / risk if risk > 0 else 0.0, regime, c.score))
    trades.sort(key=lambda t: t.exit_date)
    return {
        "params": p.model_dump(mode="json"), "sessions": len(sessions), "signals": signals,
        "metrics": metrics(trades, p),
        "trades": [t.__dict__ | {"signal_date": t.signal_date.isoformat(), "entry_date": t.entry_date.isoformat(),
                                 "exit_date": t.exit_date.isoformat()} for t in trades],
        "limitations": [
            "Analyst consensus and float are excluded (no point-in-time history from providers).",
            "Universe limited to securities ingested by this system; earlier history may carry survivorship bias.",
            "Options volume/IV history depends on ORATS historical coverage for each date.",
            "Same-bar stop/target conflicts are resolved as stop-outs.",
        ],
    }


__all__ = ["BacktestParams", "PointInTimeDataSource", "run_backtest", "simulate_trade", "metrics", "Provenance"]
