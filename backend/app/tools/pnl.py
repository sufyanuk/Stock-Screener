"""P&L engine for the trading journal. FIFO lot matching per ticker, long and short.

Inputs are journal fills (buy/sell with quantity, price, fees) plus dividends. Produces realized
trades, open positions with unrealized P&L at supplied marks, and summary statistics.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

import pandas as pd


@dataclass
class Fill:
    id: int
    ticker: str
    side: str  # "buy" | "sell"
    quantity: float
    price: float
    fees: float
    date: date
    recommendation_id: int | None = None


@dataclass
class Dividend:
    ticker: str
    amount: float
    date: date


def match_fills(fills: list[Fill]) -> tuple[list[dict], dict[str, dict]]:
    lots: dict[str, deque] = defaultdict(deque)  # each lot: [signed_qty, price, fee_per_unit, date, rec_id]
    closed: list[dict] = []
    for f in sorted(fills, key=lambda f: (f.date, f.id)):
        signed = f.quantity if f.side == "buy" else -f.quantity
        fee_unit = f.fees / f.quantity if f.quantity else 0
        q = lots[f.ticker]
        remaining = signed
        while q and remaining and (q[0][0] > 0) != (remaining > 0):
            lot = q[0]
            m = min(abs(lot[0]), abs(remaining))
            direction = 1 if lot[0] > 0 else -1
            gross = direction * (f.price - lot[1]) * m
            fees = (lot[2] + fee_unit) * m
            closed.append({"ticker": f.ticker, "direction": "long" if direction > 0 else "short", "quantity": m,
                           "entry_date": lot[3].isoformat(), "exit_date": f.date.isoformat(), "entry_price": lot[1],
                           "exit_price": f.price, "fees": round(fees, 2), "pnl": round(gross - fees, 2),
                           "return_pct": round((gross - fees) / (lot[1] * m) * 100, 3),
                           "recommendation_id": lot[4]})
            lot[0] -= direction * m
            remaining += direction * m
            if abs(lot[0]) < 1e-9:
                q.popleft()
        if abs(remaining) > 1e-9:
            q.append([remaining, f.price, fee_unit, f.date, f.recommendation_id])
    open_pos = {}
    for t, q in lots.items():
        if not q:
            continue
        qty = sum(l[0] for l in q)
        cost = sum(abs(l[0]) * l[1] for l in q)
        open_pos[t] = {"ticker": t, "quantity": qty, "avg_price": cost / abs(qty) if qty else 0,
                       "fees": sum(abs(l[0]) * l[2] for l in q), "opened": min(l[3] for l in q).isoformat()}
    return closed, open_pos


def summarize(fills: list[Fill], dividends: list[Dividend], marks: dict[str, float], period: str | None = None) -> dict:
    closed, open_pos = match_fills(fills)
    unreal = 0.0
    exposure = 0.0
    for t, p in open_pos.items():
        mark = marks.get(t)
        p["mark"] = mark
        if mark is None:
            p["unrealized"] = None
            continue
        p["unrealized"] = round((mark - p["avg_price"]) * p["quantity"] - p["fees"], 2)
        unreal += p["unrealized"]
        exposure += abs(p["quantity"]) * mark
    div_total = sum(d.amount for d in dividends)
    realized = sum(c["pnl"] for c in closed)
    wins = [c["pnl"] for c in closed if c["pnl"] > 0]
    losses = [c["pnl"] for c in closed if c["pnl"] <= 0]

    curve, dd = [], []
    if closed:
        s = pd.DataFrame(closed)
        s["exit_date"] = pd.to_datetime(s["exit_date"])
        daily = s.groupby("exit_date")["pnl"].sum().sort_index().cumsum()
        peak = daily.cummax()
        curve = [{"date": d.date().isoformat(), "equity": round(v, 2)} for d, v in daily.items()]
        dd = [{"date": d.date().isoformat(), "drawdown": round(v, 2)} for d, v in (daily - peak).items()]
    periods = {}
    if closed:
        s = pd.DataFrame(closed)
        s["exit_date"] = pd.to_datetime(s["exit_date"])
        for name, freq in (("daily", "D"), ("weekly", "W"), ("monthly", "M"), ("quarterly", "Q"), ("yearly", "Y")):
            g = s.groupby(s["exit_date"].dt.to_period(freq))["pnl"].sum()
            periods[name] = [{"period": str(k), "pnl": round(v, 2)} for k, v in g.items()]
    return {
        "closed_trades": closed, "open_positions": list(open_pos.values()),
        "realized_pnl": round(realized, 2), "unrealized_pnl": round(unreal, 2), "dividends": round(div_total, 2),
        "total_pnl": round(realized + unreal + div_total, 2), "total_fees": round(sum(f.fees for f in fills), 2),
        "exposure": round(exposure, 2), "win_rate_pct": round(len(wins) / len(closed) * 100, 2) if closed else None,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
        "equity_curve": curve, "drawdown_curve": dd, "periods": periods,
        "max_drawdown": round(min((d["drawdown"] for d in dd), default=0.0), 2),
    }
