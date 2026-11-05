"""Research helpers behind the stock side-panel, the upcoming-earnings screen and seasonality.

Every function records which provider supplied a value and never fills gaps with guesses:
a missing value is returned as ``None`` (shown as "—").
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import market_calendar as mc
from app.models import (AnalystSnapshot, DailyBar, EarningsEvent, InsiderTransaction, OptionsImportBatch,
                        OptionsImportRow, ShariahScreen)
from app.providers.http import ProviderError
from app.screening import insider as ins

log = logging.getLogger(__name__)
EARNINGS_REFRESH_HOURS = 12
ANALYST_TTL_HOURS = 24


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(ts: datetime | None) -> datetime | None:
    return ts if ts is None or ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def today_ny() -> date:
    return _now().astimezone(mc.NY).date()


# ------------------------------------------------------------------ earnings
async def refresh_earnings(db: Session, ds, force: bool = False, horizon_days: int = 21) -> dict:
    last = _aware(db.scalar(select(func.max(EarningsEvent.retrieved_at))))
    stored = db.scalars(select(EarningsEvent.provider).order_by(EarningsEvent.retrieved_at.desc()).limit(1)).first()
    several = sum(1 for a in ("fmp", "finnhub", "av") if getattr(ds, a, None) is not None) > 1
    if several and stored and "+" not in stored:
        force = True  # calendar was stored from a single source: rebuild it from all of them once
    if several and last and _now() - last > timedelta(hours=1):
        soon = db.scalar(select(func.count()).select_from(EarningsEvent).where(
            EarningsEvent.report_date >= today_ny(), EarningsEvent.report_date <= today_ny() + timedelta(days=2)))
        if (soon or 0) < 10:
            force = True  # the next days look truncated (a provider row cap): rebuild
    if not force and last and _now() - last < timedelta(hours=EARNINGS_REFRESH_HOURS):
        return {"refreshed": False, "retrieved_at": last.isoformat()}
    start = today_ny()
    try:
        rows, prov = await asyncio.wait_for(ds.earnings_calendar(start, start + timedelta(days=horizon_days)), 60)
    except (ProviderError, asyncio.TimeoutError) as e:
        return {"refreshed": False, "error": str(e) or "timed out", "retrieved_at": last.isoformat() if last else None}
    db.execute(delete(EarningsEvent).where(EarningsEvent.report_date >= start))
    seen = set()
    for r in rows:
        key = (r["symbol"], r["report_date"])
        if key in seen:
            continue
        seen.add(key)
        db.add(EarningsEvent(symbol=r["symbol"], name=r.get("name"), report_date=date.fromisoformat(r["report_date"]),
                             fiscal_date_ending=r.get("fiscal_date_ending"), eps_estimate=r.get("eps_estimate"),
                             time_of_day=r.get("time_of_day"), provider=prov.provider))
    db.commit()
    return {"refreshed": True, "rows": len(seen), "provider": prov.provider}


def upcoming_earnings(db: Session, days: int = 7) -> list[EarningsEvent]:
    start = today_ny()
    return db.scalars(select(EarningsEvent).where(EarningsEvent.report_date >= start,
                                                  EarningsEvent.report_date <= start + timedelta(days=days))
                      .order_by(EarningsEvent.report_date, EarningsEvent.symbol)).all()


def earnings_markers(db: Session, days: int = 7) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for e in upcoming_earnings(db, days):
        out.setdefault(e.symbol, {"date": e.report_date.isoformat(), "time_of_day": e.time_of_day})
    return out


# ------------------------------------------------------------------ analyst counts (cached)
def analyst_counts(db: Session, symbols: list[str]) -> dict[str, AnalystSnapshot]:
    if not symbols:
        return {}
    return {a.symbol: a for a in db.scalars(select(AnalystSnapshot).where(AnalystSnapshot.symbol.in_(symbols))).all()}


async def load_analyst_counts(db: Session, ds, symbols: list[str], limit: int = 100) -> dict:
    have = analyst_counts(db, symbols)
    cutoff = _now() - timedelta(hours=ANALYST_TTL_HOURS)
    todo = [s for s in symbols if s not in have or _aware(have[s].retrieved_at) < cutoff][:limit]
    done, errors = 0, []
    for s in todo:
        try:
            ad, prov = await ds.analyst(s)
        except ProviderError as e:
            errors.append(str(e))
            if "not configured" in str(e):
                break
            continue
        c = ad.counts or {}
        db.merge(AnalystSnapshot(symbol=s, strong_buy=c.get("strong_buy"), buy=c.get("buy"), hold=c.get("hold"),
                                 sell=c.get("sell"), strong_sell=c.get("strong_sell"), provider=prov.provider,
                                 retrieved_at=_now()))
        done += 1
    db.commit()
    remaining = len([s for s in symbols if s not in have]) - done
    return {"fetched": done, "remaining": max(0, remaining), "errors": errors[:5]}


# ------------------------------------------------------------------ shares outstanding (cached)
SHARES_TTL_DAYS = 30


def shares_map(db: Session, tickers: set[str]) -> dict[str, float | None]:
    from app.models import SharesOutstanding

    if not tickers:
        return {}
    return {r.ticker: r.shares for r in db.scalars(select(SharesOutstanding).where(SharesOutstanding.ticker.in_(tickers))).all()}


async def load_shares(db: Session, ds, tickers: list[str], limit: int = 60, max_seconds: float = 50) -> dict:
    """Look up shares outstanding for tickers not cached (or older than 30 days), concurrently."""
    from app.models import SharesOutstanding

    if not hasattr(ds, "shares_outstanding"):
        return {"fetched": 0, "remaining": 0, "errors": []}
    have = {r.ticker: r for r in db.scalars(select(SharesOutstanding).where(SharesOutstanding.ticker.in_(tickers))).all()}
    cutoff = _now() - timedelta(days=SHARES_TTL_DAYS)
    todo = [t for t in dict.fromkeys(tickers) if t not in have or _aware(have[t].retrieved_at) < cutoff]
    batch = todo[:limit]
    sem = asyncio.Semaphore(6)
    found: dict[str, tuple[float | None, str | None]] = {}
    errors: list[str] = []

    async def one(t):
        async with sem:
            try:
                found[t] = await asyncio.wait_for(ds.shares_outstanding(t), 15)
            except (ProviderError, asyncio.TimeoutError) as e:
                errors.append(f"{t}: {str(e) or 'timed out'}")
                found[t] = (None, None)

    try:
        await asyncio.wait_for(asyncio.gather(*(one(t) for t in batch)), max_seconds)
    except asyncio.TimeoutError:
        pass
    for t, (n, src) in found.items():
        db.merge(SharesOutstanding(ticker=t, shares=n, source=src, retrieved_at=_now()))
    db.commit()
    return {"fetched": sum(1 for n, _ in found.values() if n), "remaining": max(0, len(todo) - len(found)),
            "errors": errors[:5]}


# ------------------------------------------------------------------ insider / options context
def insider_summary(db: Session, symbols: list[str], days: int = 90) -> dict[str, dict]:
    if not symbols:
        return {}
    since = today_ny() - timedelta(days=days)
    rows = db.scalars(select(InsiderTransaction).where(InsiderTransaction.ticker.in_(symbols),
                                                       InsiderTransaction.transaction_date >= since)).all()
    by: dict[str, list] = defaultdict(list)
    for r in rows:
        by[r.ticker].append(ins.InsiderTxn(**{c: getattr(r, c) for c in ins.InsiderTxn.__dataclass_fields__}))
    out = {}
    for t, txns in by.items():
        purchases, _ = ins.dedupe_purchases(txns)
        if purchases:
            out[t] = {"purchases": len(purchases), "shares": sum(p.shares for p in purchases),
                      "value": sum((p.primary.value or 0) for p in purchases),
                      "latest": max(p.primary.transaction_date for p in purchases).isoformat(),
                      "insiders": len({p.primary.owner_cik for p in purchases})}
    return out


def latest_import(db: Session) -> OptionsImportBatch | None:
    return db.scalars(select(OptionsImportBatch).order_by(OptionsImportBatch.session_date.desc(),
                                                          OptionsImportBatch.id.desc()).limit(1)).first()


def options_rows(db: Session, symbols: list[str]) -> tuple[OptionsImportBatch | None, dict[str, OptionsImportRow]]:
    b = latest_import(db)
    if not b or not symbols:
        return b, {}
    rows = db.scalars(select(OptionsImportRow).where(OptionsImportRow.batch_id == b.id,
                                                     OptionsImportRow.symbol.in_(symbols))).all()
    return b, {r.symbol: r for r in rows}


def shariah_map(db: Session, symbols: set[str]) -> dict[str, str]:
    if not symbols:
        return {}
    rows = db.execute(select(ShariahScreen.ticker, ShariahScreen.status).where(ShariahScreen.ticker.in_(symbols))
                      .order_by(ShariahScreen.id)).all()
    return {t: s for t, s in rows}


# ------------------------------------------------------------------ stock side-panel
async def stock_summary(db: Session, ds, ticker: str) -> dict:
    t = ticker.upper()
    out: dict = {"ticker": t, "errors": [], "sources": {}}

    async def safe(name, coro, timeout=12):
        try:
            return await asyncio.wait_for(coro, timeout)
        except (ProviderError, asyncio.TimeoutError, Exception) as e:  # one failing source never breaks the panel
            out["errors"].append(f"{name}: {str(e) or type(e).__name__}")
            return None

    async def none():
        return None

    ss_on = getattr(ds, "sentisense", None) is not None and hasattr(ds, "sentisense_info")
    q, an, fl, news, ssr = await asyncio.gather(
        safe("quote", ds.company_quote(t)), safe("analysts", ds.analyst(t)),
        safe("float", ds.float_info(t)), safe("news", ds.news(t)),
        safe("sentisense", ds.sentisense_info(t), timeout=25) if ss_on else none())
    out["sentisense"] = ssr[0] if ssr else None

    # price & P/E
    quote = q[0] if q else {}
    if q:
        out["sources"]["quote"] = q[1].to_dict()
    b, orow = options_rows(db, [t])
    orow = orow.get(t)
    price, price_src = quote.get("price"), (q[1].provider if q and quote.get("price") else None)
    if price is None and orow and orow.price is not None:
        price, price_src = orow.price, f"CSV import #{b.id} ({b.session_date.isoformat()})"
    out.update({"name": quote.get("name"), "price": price, "price_source": price_src,
                "day_change_pct": quote.get("day_change_pct") if quote.get("price") else (orow.pct_change if orow else None),
                "pe": quote.get("pe"), "market_cap": quote.get("market_cap"),
                "year_high": quote.get("year_high"), "year_low": quote.get("year_low")})

    # float ratio
    finfo = fl[0] if fl else {}
    so, fs = finfo.get("outstanding_shares"), finfo.get("float_shares")
    out["float_ratio_pct"] = (fs / so * 100) if so and fs else None
    out["float_shares"], out["shares_outstanding"] = fs, so

    # analysts
    counts = (an[0].counts if an else None) or {}
    targets = (an[0].targets if an else None) or {}
    if counts:
        db.merge(AnalystSnapshot(symbol=t, strong_buy=counts.get("strong_buy"), buy=counts.get("buy"),
                                 hold=counts.get("hold"), sell=counts.get("sell"), strong_sell=counts.get("strong_sell"),
                                 provider=an[1].provider, retrieved_at=_now()))
        db.commit()
    out["analysts"] = {"counts": counts or None, "total": sum(counts.values()) if counts else None,
                       "recommending_buy": (counts.get("strong_buy", 0) + counts.get("buy", 0)) if counts else None,
                       "target_avg": targets.get("avg"), "target_high": targets.get("high"), "target_low": targets.get("low")}

    # earnings
    nxt = db.scalars(select(EarningsEvent).where(EarningsEvent.symbol == t, EarningsEvent.report_date >= today_ny())
                     .order_by(EarningsEvent.report_date).limit(1)).first()
    if nxt:
        out["earnings"] = {"date": nxt.report_date.isoformat(), "time_of_day": nxt.time_of_day,
                           "eps_estimate": nxt.eps_estimate, "source": nxt.provider}
    elif quote.get("earnings_announcement"):
        out["earnings"] = {"date": str(quote["earnings_announcement"])[:10], "time_of_day": None, "eps_estimate": None,
                           "source": q[1].provider}
    else:
        out["earnings"] = None

    # chart: stored bars, else fetch one year
    since = today_ny() - timedelta(days=370)
    bars = db.scalars(select(DailyBar).where(DailyBar.ticker == t, DailyBar.session >= since)
                      .order_by(DailyBar.session)).all()
    if len(bars) < 30:
        res = await safe("chart", ds.bars(t, since, today_ny()), timeout=15)
        if res and len(res[0]):
            df, prov = res
            for ts, r in df.iterrows():
                db.merge(DailyBar(ticker=t, session=ts.date(), open=float(r.open), high=float(r.high), low=float(r.low),
                                  close=float(r.close), volume=float(r.volume), provider=prov.provider))
            db.commit()
            bars = db.scalars(select(DailyBar).where(DailyBar.ticker == t, DailyBar.session >= since)
                              .order_by(DailyBar.session)).all()
    out["bars"] = [{"time": x.session.isoformat(), "open": x.open, "high": x.high, "low": x.low, "close": x.close,
                    "volume": x.volume} for x in bars]
    out["bars_provider"] = bars[-1].provider if bars else None

    out["news"] = (news[0] if news else [])[:8]
    out["options"] = None if not orow else {
        "vol_change_1m_pct": orow.vol_change_1m_pct, "options_volume": orow.options_volume, "iv_rank": orow.iv_rank,
        "put_call": orow.pc_ratio, "session": b.session_date.isoformat()}
    out["insider_90d"] = insider_summary(db, [t]).get(t)
    out["shariah_status"] = shariah_map(db, {t}).get(t)
    return out
