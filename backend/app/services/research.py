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
    if several and last and _now() - last > timedelta(minutes=15):
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
    from app.db import upsert

    db.execute(delete(EarningsEvent).where(EarningsEvent.report_date >= start))
    seen, new = set(), []
    for r in rows:
        key = (r["symbol"], r["report_date"])
        if key in seen:
            continue
        seen.add(key)
        new.append({"symbol": r["symbol"], "name": r.get("name"), "report_date": date.fromisoformat(r["report_date"]),
                    "fiscal_date_ending": r.get("fiscal_date_ending"), "eps_estimate": r.get("eps_estimate"),
                    "time_of_day": r.get("time_of_day"), "provider": prov.provider, "retrieved_at": _now()})
    # another request may be refreshing the calendar at the same moment: insert-or-update by (symbol, date)
    upsert(db, EarningsEvent, new, conflict=["symbol", "report_date"])
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


async def load_analyst_counts(db: Session, ds, symbols: list[str], limit: int = 100, max_seconds: float = 120) -> dict:
    """Analyst rating counts for stocks not fetched in the last day. Stops after ``max_seconds`` (the
    provider is rate-limited) and saves every 20 stocks, so a batch never runs into the request limit."""
    import time

    from app.db import upsert

    started = time.time()
    have = analyst_counts(db, symbols)
    cutoff = _now() - timedelta(hours=ANALYST_TTL_HOURS)
    todo = [s for s in symbols if s not in have or _aware(have[s].retrieved_at) < cutoff][:limit]
    done, errors, rows = 0, [], []
    for s in todo:
        if time.time() - started > max_seconds:
            break
        try:
            ad, prov = await asyncio.wait_for(ds.analyst(s), 20)
        except (ProviderError, asyncio.TimeoutError) as e:
            errors.append(f"{s}: {str(e) or 'timed out'}")
            if "not configured" in str(e):
                break
            continue
        c = ad.counts or {}
        rows.append({"symbol": s, "strong_buy": c.get("strong_buy"), "buy": c.get("buy"), "hold": c.get("hold"),
                     "sell": c.get("sell"), "strong_sell": c.get("strong_sell"), "provider": prov.provider,
                     "retrieved_at": _now()})
        done += 1
        if len(rows) >= 20:
            upsert(db, AnalystSnapshot, rows)
            db.commit()
            rows = []
    upsert(db, AnalystSnapshot, rows)
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


SHARES_RETRY_HOURS = 1  # a lookup that found nothing is retried after an hour (providers fail temporarily)


def _sec_shares(db: Session, tickers: list[str]) -> dict[str, tuple[float, str]]:
    """Shares outstanding from SEC figures already downloaded by the Shariah loader: the latest balance
    sheet, else the filing's cover page, else the quarter's weighted-average basic shares."""
    from app.models import SecFact, Security

    by_cik: dict[str, list[str]] = {}
    for s in db.scalars(select(Security).where(Security.ticker.in_(tickers), Security.cik.is_not(None))).all():
        by_cik.setdefault(s.cik.zfill(10), []).append(s.ticker)
    if not by_cik:
        return {}
    tags = ["Assets", "CommonStockSharesOutstanding", "dei:EntityCommonStockSharesOutstanding",
            "WeightedAverageNumberOfSharesOutstandingBasic"]
    facts: dict[str, dict] = {}
    for f in db.scalars(select(SecFact).where(SecFact.cik.in_(list(by_cik)), SecFact.tag.in_(tags))).all():
        facts.setdefault(f.cik, {})[f.tag] = f
    out = {}
    for cik, f in facts.items():
        end = f["Assets"].end if "Assets" in f else None
        bs, cover, wavg = f.get("CommonStockSharesOutstanding"), f.get("dei:EntityCommonStockSharesOutstanding"), \
            f.get("WeightedAverageNumberOfSharesOutstandingBasic")
        pick = None
        if bs and bs.val and end and bs.end == end:
            pick = (bs.val, "sec")
        elif cover and cover.val:
            pick = (cover.val, "sec (cover page)")
        elif wavg and wavg.val:
            pick = (wavg.val, "sec (weighted average)")
        if pick:
            for t in by_cik[cik]:
                out[t] = pick
    return out


async def _sec_filing_shares(db: Session, ds, tickers: list[str], max_seconds: float) -> dict[str, tuple[float, str]]:
    """Shares outstanding from each company's SEC filings (cover page, all share classes added up)."""
    from app.models import Security
    from app.providers.sec_edgar import shares_outstanding as cover_shares

    sec = getattr(ds, "sec", None)
    if sec is None or not tickers:
        return {}
    ciks = {s.ticker: s.cik for s in db.scalars(select(Security).where(Security.ticker.in_(tickers),
                                                                      Security.cik.is_not(None))).all()}
    sem = asyncio.Semaphore(6)
    out: dict[str, tuple[float, str]] = {}

    async def one(t):
        async with sem:
            try:
                cf = await asyncio.wait_for(sec.companyfacts(ciks[t]), 20)
                n, _ = cover_shares(cf, today_ny())
                if n:
                    out[t] = (n, "sec (filings)")
            except Exception:  # noqa: BLE001 - try the next source
                pass

    try:
        await asyncio.wait_for(asyncio.gather(*(one(t) for t in tickers if t in ciks)), max_seconds)
    except asyncio.TimeoutError:
        pass
    return out


def _save_shares(db: Session, found: dict[str, tuple[float | None, str | None]]) -> None:
    from app.db import upsert
    from app.models import SharesOutstanding

    upsert(db, SharesOutstanding, [{"ticker": t, "shares": n, "source": src, "retrieved_at": _now()}
                                   for t, (n, src) in found.items()])
    db.commit()


async def load_shares(db: Session, ds, tickers: list[str], limit: int = 60, max_seconds: float = 50) -> dict:
    """Shares outstanding for tickers not cached: SEC filings first (already stored), then providers
    concurrently. Found values are kept 30 days; a lookup that found nothing is retried after an hour and a
    lookup that timed out (rate-limited provider queue) is not recorded, so the next batch continues it."""
    from app.models import SharesOutstanding

    have = {r.ticker: r for r in db.scalars(select(SharesOutstanding).where(SharesOutstanding.ticker.in_(tickers))).all()}
    now = _now()

    def due(t):
        r = have.get(t)
        if r is None:
            return True
        ttl = timedelta(days=SHARES_TTL_DAYS) if r.shares else timedelta(hours=SHARES_RETRY_HOURS)
        return _aware(r.retrieved_at) < now - ttl

    todo = [t for t in dict.fromkeys(tickers) if due(t)]
    found: dict[str, tuple[float | None, str | None]] = dict(_sec_shares(db, todo))
    rest = [t for t in todo if t not in found]
    if rest:  # each company's own SEC filings (free, every SEC filer)
        found |= await _sec_filing_shares(db, ds, rest[:limit], max_seconds / 2)
        rest = [t for t in todo if t not in found]
    errors: list[str] = []
    if rest and hasattr(ds, "shares_outstanding"):
        sem = asyncio.Semaphore(6)

        async def one(t):
            async with sem:
                try:
                    found[t] = await asyncio.wait_for(ds.shares_outstanding(t), 30)
                except ProviderError as e:  # every provider answered: no figure
                    errors.append(f"{t}: {e}")
                    found[t] = (None, None)
                except asyncio.TimeoutError:  # still queued behind the provider rate limit: try next batch
                    errors.append(f"{t}: timed out")

        try:
            await asyncio.wait_for(asyncio.gather(*(one(t) for t in rest[:limit])), max_seconds)  # unfinished: next batch
        except asyncio.TimeoutError:
            pass
    _save_shares(db, found)
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


NON_COMPLIANT = "Non-Compliant"


def attach_prices(db: Session, rows: list[dict], key: str = "symbol", field: str = "price") -> list[dict]:
    """Add each row's latest stored price (``price``, ``price_as_of``) from the nightly price refresh — no API
    call. Rows that already carry a price (e.g. from an imported CSV or the scan) keep it."""
    from app.models import PriceSnapshot

    need = {r.get(key) for r in rows if r.get(key) and r.get(field) is None}
    found: dict[str, tuple] = {}
    sl = sorted(need)
    for i in range(0, len(sl), 1000):
        for sym, c, d in db.execute(select(PriceSnapshot.symbol, PriceSnapshot.close, PriceSnapshot.as_of).where(
                PriceSnapshot.symbol.in_(sl[i:i + 1000]), PriceSnapshot.close > 0)).all():
            found[sym] = (c, d)
    for r in rows:
        if r.get(field) is None and r.get(key) in found:
            c, d = found[r[key]]
            r[field], r[f"{field}_as_of"] = c, d.isoformat() if d else None
        r.setdefault(field, None)
    return rows


def hide_non_compliant(db: Session, rows: list[dict], key: str = "symbol") -> list[dict]:
    """Scan results never show stocks that are NON-COMPLIANT under the chosen Shariah methodology
    (doubtful and not-yet-screened stocks stay visible, coloured amber / blue)."""
    sh = shariah_map(db, {r[key] for r in rows if r.get(key)})
    out = []
    for r in rows:
        st = sh.get(r.get(key))
        if st == NON_COMPLIANT:
            continue
        r["shariah_status"] = st
        out.append(r)
    return out


def _trade_plan(db: Session, bars: list) -> dict | None:
    """Stop loss and Target 1 from the daily bars, by the same rules as the scan's trade plan (stop just below
    the nearest validated support zone, Target 1 = next resistance zone). None when there is no support zone
    or too little history: nothing is invented."""
    if len(bars) < 60:
        return None
    import pandas as pd

    from app.screening import technicals as tech
    from app.services.engine import load_config

    df = pd.DataFrame([{"open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume} for b in bars],
                      index=pd.to_datetime([b.session for b in bars]))
    try:
        plan = tech.analyze(df, load_config(db).technical).get("trade_plan")
    except Exception:  # noqa: BLE001 - a bad series never breaks the panel
        return None
    if not plan:
        return None
    return {"stop_loss": plan["stop_loss"], "target1": plan.get("target1"), "entry": plan["entry"],
            "as_of": df.index[-1].date().isoformat(), "method": plan.get("method")}


def shariah_map(db: Session, symbols: set[str]) -> dict[str, str]:
    """Colour status per ticker from the internal Shariah engine, for the methodology chosen in Settings;
    tickers it has not screened yet fall back to the latest scan-time screen."""
    if not symbols:
        return {}
    from app.services import shariah as shs
    from app.services.engine import load_config

    engine = shs.status_map(db, set(symbols), load_config(db).shariah.methodology)
    rest = set(symbols) - set(engine)  # scan-time history only for tickers the engine has not screened
    rows = db.execute(select(ShariahScreen.ticker, ShariahScreen.status).where(ShariahScreen.ticker.in_(rest))
                      .order_by(ShariahScreen.id)).all() if rest else []
    out = {t: s for t, s in rows}
    out.update(engine)
    return out


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
    q, an, news, ssr = await asyncio.gather(
        safe("quote", ds.company_quote(t)), safe("analysts", ds.analyst(t)), safe("news", ds.news(t)),
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

    # debt-to-equity: total debt ÷ shareholders' equity (Finnhub, latest quarter, else latest year)
    out["debt_to_equity"] = quote.get("debt_to_equity")

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
    out["trade_plan"] = _trade_plan(db, bars)

    out["news"] = (news[0] if news else [])[:8]
    out["options"] = None if not orow else {
        "vol_change_1m_pct": orow.vol_change_1m_pct, "options_volume": orow.options_volume, "iv_rank": orow.iv_rank,
        "put_call": orow.pc_ratio, "session": b.session_date.isoformat()}
    out["insider_90d"] = insider_summary(db, [t]).get(t)
    out["shariah_status"] = shariah_map(db, {t}).get(t)
    return out
