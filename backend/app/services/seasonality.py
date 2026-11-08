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


API_TICKER_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")
API_YEARS = 10
API_BATCH = 200


def _rebuild_market_months(db: Session, source: str) -> None:
    db.execute(delete(MarketMonth))
    now = datetime.now(timezone.utc)
    counts = db.execute(select(MonthlyClose.month, func.count()).group_by(MonthlyClose.month)).all()
    if counts:
        db.execute(insert(MarketMonth), [{"month": m, "session": month_last_session(m), "symbols": n,
                                          "provider": source[:32], "retrieved_at": now} for m, n in counts])
    db.commit()


def _last_complete_month() -> date:
    t = _today()
    return date(t.year - 1, 12, 1) if t.month == 1 else date(t.year, t.month - 1, 1)


def api_status(db: Session) -> dict:
    from app.models import Security, SeasonalitySymbol

    uni = {t for t in db.scalars(select(Security.ticker)).all() if t and API_TICKER_RE.match(t)}
    rows = db.execute(select(SeasonalitySymbol.symbol, SeasonalitySymbol.last_month)).all()
    loaded = {r.symbol for r in rows}
    target = _last_complete_month()
    return {"universe": len(uni), "loaded": len(loaded & uni) if uni else len(loaded),
            "remaining": len(uni - loaded),
            "stale": sum(1 for r in rows if r.last_month and r.last_month < target), "target_month": target.isoformat()[:7]}


async def load_api(db: Session, ds, max_seconds: float = 200.0, rebuild: bool = False) -> dict:
    """Month-end closes for every listed US common stock from Alpaca monthly bars (200 stocks per
    request, adjusted for splits and dividends, ~10 years). Resumable within a time budget; months that
    already exist for a stock (e.g. from an imported CSV) are kept. Rebuilds the statistics at the end."""
    import asyncio
    import time

    from app.models import Security, SeasonalitySymbol

    started = time.time()
    alp = getattr(ds, "alpaca", None)
    if alp is None or not hasattr(alp, "multi_daily_bars"):
        return {"processed": 0, "errors": ["Alpaca keys are not set"]} | api_status(db) | {"coverage": coverage(db)}
    if rebuild:
        db.execute(delete(SeasonalitySymbol))
        db.execute(delete(MonthlyClose).where(MonthlyClose.provider == "alpaca"))
        db.commit()
    target = _last_complete_month()
    end = date(target.year + (target.month == 12), 1 if target.month == 12 else target.month + 1, 1) - timedelta(days=1)
    uni = sorted({t for t in db.scalars(select(Security.ticker)).all() if t and API_TICKER_RE.match(t)})
    done = {r.symbol: r for r in db.scalars(select(SeasonalitySymbol)).all()}
    todo = [(s, date(target.year - API_YEARS, target.month, 1)) for s in uni if s not in done]
    todo += [(s, r.last_month) for s, r in done.items() if r.last_month and r.last_month < target]
    errors: list[str] = []
    processed = 0
    batches = [todo[i:i + API_BATCH] for i in range(0, len(todo), API_BATCH)]
    budget = max(10.0, max_seconds - 45)  # keep time to rebuild the statistics
    for i in range(0, len(batches), 4):
        if time.time() - started > budget:
            break
        group = batches[i:i + 4]
        res = await asyncio.gather(*(alp.multi_daily_bars([s for s, _ in b], min(f for _, f in b), end, "1Month")
                                     for b in group), return_exceptions=True)
        now = datetime.now(timezone.utc)
        for b, bars in zip(group, res):
            if isinstance(bars, Exception):
                errors.append(str(bars)[:200])
                continue
            syms = [s for s, _ in b]
            have = set(db.execute(select(MonthlyClose.symbol, MonthlyClose.month)
                                  .where(MonthlyClose.symbol.in_(syms))).all())
            new_rows = []
            for s in syms:
                for d, o, h, lo, c, v in bars.get(s, []):
                    m = date.fromisoformat(d[:7] + "-01")
                    if m > target or not c or c <= 0 or (s, m) in have:
                        continue
                    new_rows.append({"symbol": s, "month": m, "close": float(c), "provider": "alpaca", "retrieved_at": now})
                    have.add((s, m))
            if new_rows:
                db.execute(insert(MonthlyClose), new_rows)
            for s in syms:
                months = [m for (sym, m) in have if sym == s]
                db.merge(SeasonalitySymbol(symbol=s, months=len(months), last_month=max(months) if months else target,
                                           provider="alpaca", retrieved_at=now))
            db.commit()
            processed += len(syms)
        if errors and not processed:
            break
    # Recompute the statistics once, when the load has finished (it reads every month-end close).
    if processed and api_status(db)["remaining"] == 0:
        _rebuild_market_months(db, "alpaca (monthly bars)")
        recompute(db)
    return {"processed": processed, "errors": errors[:3]} | api_status(db) | {"coverage": coverage(db)}


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
