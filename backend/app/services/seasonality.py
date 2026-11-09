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
Imports add months: a month already stored for a stock is kept as it is, and only the calendar months
that gained data are recalculated (adding November changes only the November statistics), so earlier
results stay intact. "Start over" replaces the whole history and recalculates everything.
Ranking: by average return (or % of years positive) among stocks with at least ``min_years``.
If the CSV has an adjusted-close column it is used (returns then include dividends); otherwise
the close column (price returns). Past seasonality does not guarantee future returns.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import bindparam, delete, extract, func, insert, select, update
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


PENDING_KEY = "seasonality_pending"  # (stock -> calendar months) that gained data in the import in progress


def import_chunk(db: Session, rows: list[list], replace_all: bool, source: str) -> dict:
    """Store month-end closes ``[symbol, "YYYY-MM", close]``. ``replace_all`` (first chunk of a "start over"
    import) wipes the stored history first. Otherwise only months not yet stored are added: months already
    stored are kept unchanged, so their statistics stay as they are."""
    from app.db import upsert
    from app.models import KeyValue

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
    pending = db.get(KeyValue, PENDING_KEY)
    state = dict(pending.value) if pending and not replace_all else {"full": False, "keys": {}}
    if replace_all:
        db.execute(delete(SeasonalityStat))
        db.execute(delete(MarketMonth))
        db.execute(delete(MonthlyClose))
        state["full"] = True
        existing: set[tuple[str, date]] = set()
    else:
        existing = set()
        sl = sorted(syms)
        for i in range(0, len(sl), 1000):
            existing |= set(db.execute(select(MonthlyClose.symbol, MonthlyClose.month)
                                       .where(MonthlyClose.symbol.in_(sl[i:i + 1000]))).all())
    now = datetime.now(timezone.utc)
    new = {k: c for k, c in clean.items() if k not in existing}
    vals = [{"symbol": s, "month": m, "close": c, "provider": source[:32], "retrieved_at": now} for (s, m), c in new.items()]
    for i in range(0, len(vals), 20_000):
        db.execute(insert(MonthlyClose), vals[i:i + 20_000])
    keys = state.setdefault("keys", {})
    for s, m in new:
        if m.month not in keys.setdefault(s, []):
            keys[s].append(m.month)
    upsert(db, KeyValue, [{"key": PENDING_KEY, "value": state, "updated_at": now}])
    db.commit()
    return {"stored": len(vals), "already_stored": len(clean) - len(new), "symbols": len(syms), "skipped": skipped}


def finish_import(db: Session, source: str, last_date: date | None) -> dict:
    """Drop a partial final month, record which months are loaded, and recalculate: everything after a
    "start over" import, otherwise only the stocks and calendar months that gained data."""
    from app.models import KeyValue

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
    pending = db.get(KeyValue, PENDING_KEY)
    state = dict(pending.value) if pending else {"full": True, "keys": {}}
    if pending:
        db.delete(pending)
    db.commit()
    keys = {s: set(c) for s, c in (state.get("keys") or {}).items()}
    if state.get("full") or not db.scalar(select(SeasonalityStat.symbol).limit(1)):
        return {"stats": recompute(db), "mode": "full", "partial_month_dropped": dropped}
    if not keys:
        return {"stats": 0, "mode": "nothing new", "partial_month_dropped": dropped}
    return {"stats": recompute(db, keys), "mode": "incremental", "new_stock_months": sum(map(len, keys.values())),
            "partial_month_dropped": dropped}


def months_loaded(db: Session) -> set[date]:
    return set(db.scalars(select(MarketMonth.month)).all())


def _prev_cal(m: int) -> int:
    return 12 if m == 1 else m - 1


def recompute(db: Session, only: dict[str, set[int]] | None = None) -> int:
    """Rebuild seasonality_stats from monthly_closes (vectorised with pandas). With ``only`` (stock ->
    calendar months that gained data) just those stocks' statistics for those calendar months are
    recalculated, reading only the closes they need (that month and the month before, in every year);
    every other statistic is left unchanged."""
    q = (select(MonthlyClose.symbol, MonthlyClose.month, MonthlyClose.close)
         .where(MonthlyClose.month.in_(select(MarketMonth.month))))
    syms: list[str] = []
    cals: set[int] = set()
    if only is not None:
        syms = sorted(only)
        cals = set().union(*only.values()) if only else set()
        need = cals | {_prev_cal(c) for c in cals}
        q = q.where(extract("month", MonthlyClose.month).in_(sorted(need)))
        frames = [pd.DataFrame(db.execute(q.where(MonthlyClose.symbol.in_(syms[i:i + 1000]))).all(),
                               columns=["symbol", "month", "close"]) for i in range(0, len(syms), 1000)]
        df = pd.concat(frames) if frames else pd.DataFrame(columns=["symbol", "month", "close"])
        for i in range(0, len(syms), 1000):
            db.execute(delete(SeasonalityStat).where(SeasonalityStat.symbol.in_(syms[i:i + 1000]),
                                                     SeasonalityStat.month.in_(sorted(cals))))
    else:
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
    if only is not None:
        long = long[long["cal"].isin(cals)]
        last_close = _latest_closes(db, syms)
    g = long.groupby(["symbol", "cal"])["ret"]
    stats = pd.DataFrame({"years": g.count(), "avg": g.mean(), "median": g.median(),
                          "win_rate": g.apply(lambda s: (s > 0).mean() * 100), "best": g.max(), "worst": g.min()})
    best_idx = g.idxmax()
    worst_idx = g.idxmin()
    stats["best_year"] = long.loc[best_idx.values, "year"].values
    stats["worst_year"] = long.loc[worst_idx.values, "year"].values
    pos = long[long["ret"] > 0].groupby(["symbol", "cal"])["ret"]
    neg = long[long["ret"] < 0].groupby(["symbol", "cal"])["ret"]
    stats = stats.join(pd.DataFrame({"pos_years": pos.count(), "pos_max": pos.max(), "pos_min": pos.min(),
                                     "pos_avg": pos.mean()}))
    stats = stats.join(pd.DataFrame({"neg_years": neg.count(), "neg_max": neg.min(), "neg_min": neg.max(),
                                     "neg_avg": neg.mean()}))
    stats = stats.reset_index()
    now = datetime.now(timezone.utc)

    def opt(v, cast=float):
        return cast(v) if pd.notna(v) else None

    rows = [{"symbol": r.symbol, "month": int(r.cal), "years": int(r.years), "avg": float(r.avg),
             "median": float(r.median), "win_rate": float(r.win_rate), "best": float(r.best),
             "best_year": int(r.best_year), "worst": float(r.worst), "worst_year": int(r.worst_year),
             "last_close": float(last_close.get(r.symbol)) if pd.notna(last_close.get(r.symbol)) else None,
             "pos_years": opt(r.pos_years, int) or 0, "pos_max": opt(r.pos_max), "pos_min": opt(r.pos_min),
             "pos_avg": opt(r.pos_avg), "neg_years": opt(r.neg_years, int) or 0, "neg_max": opt(r.neg_max),
             "neg_min": opt(r.neg_min), "neg_avg": opt(r.neg_avg),
             "computed_at": now} for r in stats.itertuples(index=False)]
    from app.db import upsert

    upsert(db, SeasonalityStat, rows)  # another request may be recomputing at the same moment
    if only is not None:  # the price column's fallback (latest close) for these stocks' other months too
        lc = [{"s": s, "c": float(c)} for s, c in last_close.items() if pd.notna(c)]
        for i in range(0, len(lc), 1000):
            t = SeasonalityStat.__table__
            db.execute(update(t).where(t.c.symbol == bindparam("s")).values(last_close=bindparam("c")), lc[i:i + 1000])
    db.commit()
    return len(rows)


def _latest_closes(db: Session, syms: list[str]) -> pd.Series:
    """Latest stored month-end close per stock (one grouped query per 1,000 stocks)."""
    out: dict[str, float] = {}
    for i in range(0, len(syms), 1000):
        part = syms[i:i + 1000]
        last = (select(MonthlyClose.symbol, func.max(MonthlyClose.month).label("m"))
                .where(MonthlyClose.symbol.in_(part), MonthlyClose.month.in_(select(MarketMonth.month)))
                .group_by(MonthlyClose.symbol).subquery())
        out |= dict(db.execute(select(MonthlyClose.symbol, MonthlyClose.close).join(
            last, (MonthlyClose.symbol == last.c.symbol) & (MonthlyClose.month == last.c.m))).all())
    return pd.Series(out, dtype=float)


def coverage(db: Session) -> dict:
    first, last, months = db.execute(select(func.min(MarketMonth.month), func.max(MarketMonth.month),
                                            func.count(MarketMonth.month))).one()
    symbols = db.scalar(select(func.count(func.distinct(SeasonalityStat.symbol)))) or 0
    computed = db.scalar(select(func.max(SeasonalityStat.computed_at)))
    provider = db.scalars(select(MarketMonth.provider).order_by(MarketMonth.month.desc()).limit(1)).first()
    return {"first_month": first.isoformat() if first else None, "last_month": last.isoformat() if last else None,
            "months": months or 0, "symbols": symbols, "provider": provider,
            "computed_at": computed.isoformat() if computed else None}


def _filtered(month: int, min_years: int, min_price: float | None, max_price: float | None, q: str | None,
              symbols: set[str] | None = None):
    s = select(SeasonalityStat).where(SeasonalityStat.month == month, SeasonalityStat.years >= min_years)
    if min_price is not None:
        s = s.where(SeasonalityStat.last_close >= min_price)
    if max_price is not None:
        s = s.where(SeasonalityStat.last_close < max_price)
    if q:
        s = s.where(SeasonalityStat.symbol.like(q.upper().strip() + "%"))
    if symbols is not None:
        s = s.where(SeasonalityStat.symbol.in_(symbols or {"__none__"}))
    return s


def ranking(db: Session, month: int, min_years: int = 5, rank_by: str = "pct", limit: int = 500,
            min_price: float | None = None, max_price: float | None = None, q: str | None = None,
            mode: str = "bullish", symbols: set[str] | None = None, names: dict[str, str] | None = None) -> dict:
    """Bullish: years the month closed up (count, %, max / min / average gain).
    Bearish: years it closed down (count, %, largest / smallest / average fall).
    Sorted by % of years in the chosen direction, then the average move."""
    from app.models import Security

    bear = mode == "bearish"
    cnt = SeasonalityStat.neg_years if bear else SeasonalityStat.pos_years
    pct_expr = (func.coalesce(cnt, 0) * 100.0 / SeasonalityStat.years)
    avg_col = SeasonalityStat.neg_avg if bear else SeasonalityStat.pos_avg
    avg_order = (avg_col.asc() if bear else avg_col.desc()).nulls_last()
    order = (avg_order, pct_expr.desc()) if rank_by == "avg" else (pct_expr.desc(), avg_order)
    base = _filtered(month, min_years, min_price, max_price, q, symbols)
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = db.scalars(base.order_by(*order, SeasonalityStat.symbol).limit(limit)).all()
    syms = {r.symbol for r in rows}
    nm = dict(db.execute(select(Security.ticker, Security.name).where(Security.ticker.in_(syms))).all()) if syms else {}
    nm.update({k: v for k, v in (names or {}).items() if v})
    ranked = []
    for i, r in enumerate(rows, 1):
        n = (r.neg_years if bear else r.pos_years) or 0
        ranked.append({"rank": i, "symbol": r.symbol, "name": nm.get(r.symbol), "years": r.years,
                       "count": n, "pct": n / r.years * 100 if r.years else None,
                       "max": r.neg_max if bear else r.pos_max, "min": r.neg_min if bear else r.pos_min,
                       "avg_dir": r.neg_avg if bear else r.pos_avg,
                       "avg": r.avg, "median": r.median, "win_rate": r.win_rate, "best": r.best,
                       "best_year": r.best_year, "worst": r.worst, "worst_year": r.worst_year,
                       "last_close": r.last_close})
    return {"month": month, "month_name": MONTHS[month - 1], "mode": "bearish" if bear else "bullish",
            "rank_by": rank_by, "min_years": min_years, "total_matching": total, "ranked": ranked,
            "note": "Month-end to month-end price change in this calendar month over past years. "
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
