"""Whole-market seasonality.

Data: the split-adjusted close of **every US stock** on the last trading session of each month,
fetched with one "grouped daily" request per month (Massive; FMP bulk EOD as fallback).
Ten years = ~120 requests once, then one request per month.

Method, per stock and calendar month (Jan…Dec):
  * monthly return = close(month) / close(previous month) − 1, only for consecutive months;
  * the current (unfinished) month is excluded;
  * returns beyond ±200% in a single month are treated as probable data errors (unadjusted
    corporate actions, ticker reuse) and excluded;
  * stats: years of data, average, median, % of years positive, best and worst year.
Ranking: by average return (or % of years positive) among stocks with at least ``min_years``.
Price returns only (dividends excluded). Past seasonality does not guarantee future returns.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app import market_calendar as mc
from app.models import MarketMonth, MonthlyClose, SeasonalityStat
from app.providers.http import ProviderError

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
TICKER_RE = re.compile(r"^[A-Z]{1,5}$")  # common stocks; excludes warrants/units/preferreds (".", "-", 6+ chars)
MAX_ABS_MONTHLY_RETURN = 200.0


def _today() -> date:
    return datetime.now(timezone.utc).astimezone(mc.NY).date()


def month_end_sessions(years: int, today: date | None = None) -> list[tuple[date, date]]:
    """(first-of-month key, last trading session) for each completed month, oldest first.
    Includes the month before the window so the first month has a return."""
    today = today or _today()
    y, m = today.year, today.month  # current month is not complete -> stop at previous month
    out = []
    for _ in range(years * 12 + 1):
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        last = mc.previous_sessions(nxt - timedelta(days=1), 1)[-1]
        out.append((date(y, m, 1), last))
    return out[::-1]


def months_loaded(db: Session) -> set[date]:
    """Months loaded for the WHOLE market (rows from single-stock fetches don't count)."""
    return set(db.scalars(select(MarketMonth.month)).all())


async def refresh(db: Session, ds, years: int = 10, max_months: int = 12) -> dict:
    targets = month_end_sessions(years)
    have = months_loaded(db)
    missing = [t for t in reversed(targets) if t[0] not in have]  # newest first
    fetched, errors = 0, []
    for key, session in missing[:max_months]:
        try:
            closes, prov = await ds.market_closes(session)
        except ProviderError as e:
            errors.append(str(e))
            break
        rows = [{"symbol": s, "month": key, "close": c, "provider": prov.provider,
                 "retrieved_at": datetime.now(timezone.utc)}
                for s, c in closes.items() if TICKER_RE.match(s) and c and c > 0]
        if not rows:
            errors.append(f"no prices returned for {session.isoformat()} (market holiday or plan limit?)")
            continue
        db.execute(delete(MonthlyClose).where(MonthlyClose.month == key))
        db.execute(insert(MonthlyClose), rows)
        db.merge(MarketMonth(month=key, session=session, symbols=len(rows), provider=prov.provider,
                             retrieved_at=datetime.now(timezone.utc)))
        db.commit()
        fetched += 1
    stats_missing = db.scalar(select(func.count()).select_from(SeasonalityStat)) == 0
    if fetched or (stats_missing and months_loaded(db)):
        recompute(db)
    loaded = months_loaded(db)
    return {"fetched": fetched, "remaining": len([t for t in targets if t[0] not in loaded]),
            "months_loaded": len(loaded & {t[0] for t in targets}), "total_months": len(targets),
            "errors": errors[:3]}


def recompute(db: Session) -> int:
    """Rebuild seasonality_stats from monthly_closes (vectorised with pandas)."""
    q = (select(MonthlyClose.symbol, MonthlyClose.month, MonthlyClose.close)
         .where(MonthlyClose.month.in_(select(MarketMonth.month))))
    df = pd.DataFrame(db.execute(q).all(), columns=["symbol", "month", "close"])
    db.execute(delete(SeasonalityStat))
    if df.empty:
        db.commit()
        return 0
    df["month"] = pd.to_datetime(df["month"])
    wide = df.pivot_table(index="month", columns="symbol", values="close", aggfunc="last").sort_index()
    full = pd.date_range(wide.index.min(), wide.index.max(), freq="MS")
    wide = wide.reindex(full)  # gaps become NaN, so non-consecutive months produce no return
    last_close = wide.ffill().iloc[-1]
    rets = (wide / wide.shift(1) - 1) * 100
    cur = _today()
    rets = rets[~((rets.index.year == cur.year) & (rets.index.month == cur.month))]
    long = rets.stack().rename("ret").reset_index()
    long.columns = ["month", "symbol", "ret"]
    long = long[long["ret"].abs() <= MAX_ABS_MONTHLY_RETURN]
    long["cal"] = long["month"].dt.month
    long["year"] = long["month"].dt.year
    g = long.groupby(["symbol", "cal"])["ret"]
    stats = pd.DataFrame({"years": g.count(), "avg": g.mean(), "median": g.median(),
                          "win_rate": g.apply(lambda s: (s > 0).mean() * 100), "best": g.max(), "worst": g.min()})
    best_idx = g.idxmax()
    worst_idx = g.idxmin()
    stats["best_year"] = long.loc[best_idx.values, "year"].values
    stats["worst_year"] = long.loc[worst_idx.values, "year"].values
    stats = stats.reset_index()
    now = datetime.now(timezone.utc)
    rows = [{"symbol": r.symbol, "month": int(r.cal), "years": int(r.years), "avg": float(r.avg),
             "median": float(r.median), "win_rate": float(r.win_rate), "best": float(r.best),
             "best_year": int(r.best_year), "worst": float(r.worst), "worst_year": int(r.worst_year),
             "last_close": float(last_close.get(r.symbol)) if pd.notna(last_close.get(r.symbol)) else None,
             "computed_at": now} for r in stats.itertuples(index=False)]
    for i in range(0, len(rows), 20_000):
        db.execute(insert(SeasonalityStat), rows[i:i + 20_000])
    db.commit()
    return len(rows)


def coverage(db: Session) -> dict:
    first, last, months = db.execute(select(func.min(MarketMonth.month), func.max(MarketMonth.month),
                                            func.count(MarketMonth.month))).one()
    symbols = db.scalar(select(func.count(func.distinct(SeasonalityStat.symbol)))) or 0
    computed = db.scalar(select(func.max(SeasonalityStat.computed_at)))
    provider = db.scalars(select(MarketMonth.provider).order_by(MarketMonth.month.desc()).limit(1)).first()
    return {"first_month": first.isoformat() if first else None, "last_month": last.isoformat() if last else None,
            "months": months or 0, "symbols": symbols, "provider": provider,
            "computed_at": computed.isoformat() if computed else None}


def _filtered(month: int, min_years: int, min_price: float | None, max_price: float | None, q: str | None):
    s = select(SeasonalityStat).where(SeasonalityStat.month == month, SeasonalityStat.years >= min_years)
    if min_price is not None:
        s = s.where(SeasonalityStat.last_close >= min_price)
    if max_price is not None:
        s = s.where(SeasonalityStat.last_close < max_price)
    if q:
        s = s.where(SeasonalityStat.symbol.like(q.upper().strip() + "%"))
    return s


def ranking(db: Session, month: int, min_years: int = 5, rank_by: str = "avg", limit: int = 200,
            min_price: float | None = None, max_price: float | None = None, q: str | None = None) -> dict:
    order = ((SeasonalityStat.win_rate.desc(), SeasonalityStat.avg.desc()) if rank_by == "win"
             else (SeasonalityStat.avg.desc(), SeasonalityStat.win_rate.desc()))
    base = _filtered(month, min_years, min_price, max_price, q)
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = db.scalars(base.order_by(*order, SeasonalityStat.symbol).limit(limit)).all()
    syms = [r.symbol for r in rows]
    heat: dict[str, list] = {s: [None] * 12 for s in syms}
    if syms:
        for st in db.scalars(select(SeasonalityStat).where(SeasonalityStat.symbol.in_(syms))).all():
            heat[st.symbol][st.month - 1] = round(st.avg, 2)
    ranked = [{"rank": i, "symbol": r.symbol, "years": r.years, "avg": r.avg, "median": r.median,
               "win_rate": r.win_rate, "best": r.best, "best_year": r.best_year, "worst": r.worst,
               "worst_year": r.worst_year, "last_close": r.last_close, "heatmap": heat[r.symbol]}
              for i, r in enumerate(rows, 1)]
    return {"month": month, "month_name": MONTHS[month - 1], "rank_by": rank_by, "min_years": min_years,
            "total_matching": total, "ranked": ranked,
            "note": "Average price return in this calendar month over past years (dividends excluded). "
                    "Past seasonality does not guarantee future returns."}


def best_by_month(db: Session, min_years: int = 5, top: int = 5, min_price: float | None = None,
                  max_price: float | None = None) -> list[dict]:
    out = []
    for m in range(1, 13):
        rows = db.scalars(_filtered(m, min_years, min_price, max_price, None)
                          .order_by(SeasonalityStat.avg.desc(), SeasonalityStat.symbol).limit(top)).all()
        out.append({"month": m, "month_name": MONTHS[m - 1],
                    "top": [{"symbol": r.symbol, "avg": round(r.avg, 2), "win_rate": round(r.win_rate, 1),
                             "years": r.years} for r in rows]})
    return out
