"""Seasonality from an imported price-history CSV.

Data: the user's CSV of historical daily (or weekly/monthly) OHLCV bars. The browser reads the file
locally and sends only the **last close of each month per stock** (so files of any size work within
the upload limit); the server stores those month-end closes and rebuilds the statistics.

Method, per stock and calendar month (Jan…Dec):
  * monthly return = close(month) / close(previous month) − 1, only for consecutive months;
  * the current (unfinished) month and a partial final month in the file are excluded;
  * returns beyond ±200% in a single month are treated as probable data errors (unadjusted
    splits, ticker reuse) and excluded;
  * stats: years of data, average, median, % of years positive, best and worst year.
Ranking: by average return (or % of years positive) among stocks with at least ``min_years``.
If the CSV has an adjusted-close column it is used (returns then include dividends); otherwise
the close column (price returns). Past seasonality does not guarantee future returns.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app import market_calendar as mc
from app.models import MarketMonth, MonthlyClose, SeasonalityStat

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
MAX_ABS_MONTHLY_RETURN = 200.0


def _today() -> date:
    return datetime.now(timezone.utc).astimezone(mc.NY).date()


def month_last_session(month: date) -> date:
    nxt = date(month.year + (month.month == 12), 1 if month.month == 12 else month.month + 1, 1)
    return mc.previous_sessions(nxt - timedelta(days=1), 1)[-1]


def import_chunk(db: Session, rows: list[list], replace_all: bool, source: str) -> dict:
    """Store month-end closes ``[symbol, "YYYY-MM", close]``. ``replace_all`` wipes the existing
    history first (first chunk of a fresh import); otherwise each symbol in the chunk replaces that
    symbol's stored history (the browser never splits one symbol across chunks)."""
    clean: dict[tuple[str, date], float] = {}
    skipped = 0
    for r in rows:
        try:
            sym, mon, close = str(r[0]).strip().upper(), MONTH_RE.match(str(r[1]).strip()), float(r[2])
        except (TypeError, ValueError, IndexError):
            skipped += 1
            continue
        if not mon or not TICKER_RE.match(sym) or not 1 <= int(mon[2]) <= 12 or not close > 0:
            skipped += 1
            continue
        clean[(sym, date(int(mon[1]), int(mon[2]), 1))] = close
    syms = {s for s, _ in clean}
    if replace_all:
        db.execute(delete(SeasonalityStat))
        db.execute(delete(MarketMonth))
        db.execute(delete(MonthlyClose))
    elif syms:
        db.execute(delete(MonthlyClose).where(MonthlyClose.symbol.in_(syms)))
    now = datetime.now(timezone.utc)
    vals = [{"symbol": s, "month": m, "close": c, "provider": source[:32], "retrieved_at": now}
            for (s, m), c in clean.items()]
    for i in range(0, len(vals), 20_000):
        db.execute(insert(MonthlyClose), vals[i:i + 20_000])
    db.commit()
    return {"stored": len(vals), "symbols": len(syms), "skipped": skipped}


def finish_import(db: Session, source: str, last_date: date | None) -> dict:
    """Drop a partial final month, record which months are loaded, and rebuild the statistics."""
    dropped = None
    last_month = db.scalar(select(func.max(MonthlyClose.month)))
    if last_month and last_date and (last_date.year, last_date.month) == (last_month.year, last_month.month) \
            and last_date < month_last_session(last_month):
        db.execute(delete(MonthlyClose).where(MonthlyClose.month == last_month))
        dropped = last_month.isoformat()[:7]
    db.execute(delete(MarketMonth))
    now = datetime.now(timezone.utc)
    counts = db.execute(select(MonthlyClose.month, func.count()).group_by(MonthlyClose.month)).all()
    if counts:
        db.execute(insert(MarketMonth), [{"month": m, "session": month_last_session(m), "symbols": n,
                                          "provider": source[:32], "retrieved_at": now} for m, n in counts])
    db.commit()
    stats = recompute(db)
    return {"stats": stats, "partial_month_dropped": dropped}


def months_loaded(db: Session) -> set[date]:
    return set(db.scalars(select(MarketMonth.month)).all())


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
            "note": "Average return in this calendar month over past years, from the imported price history. "
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
