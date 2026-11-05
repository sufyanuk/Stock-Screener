"""Ratings tab: analyst Strong Buy counts and SentiSense Ratings for the stocks the app follows.

Universe (in priority order): your custom watchlist, the latest unusual-options CSV, insider purchases
of the last 90 days, and companies reporting earnings in the next 7 days.

* Analyst ratings: Finnhub recommendation counts (FMP / Alpha Vantage fallback), cached for a day in
  ``analyst_snapshots``; the tab lists stocks with at least one Strong Buy.
* SentiSense: one screener request returns every rated stock (letter, score, rank); if the screener
  is unavailable on the plan, ratings are fetched per stock for the universe (25 per call, to respect
  the free quota of 1,000 requests a month). Cached in ``sentisense_ratings`` for a day.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import (AnalystSnapshot, CustomWatchItem, SentiSenseRatingRow, Security)
from app.providers.http import ProviderError

TTL = timedelta(hours=20)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(ts: datetime | None) -> datetime | None:
    return ts if ts is None or ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def universe(db: Session) -> list[str]:
    from app.services import research as rs

    out: dict[str, None] = {}
    for s in db.scalars(select(CustomWatchItem.symbol)).all():
        out[s] = None
    b, _ = rs.options_rows(db, ["__none__"])
    if b:
        from app.models import OptionsImportRow

        for s in db.scalars(select(OptionsImportRow.symbol).where(OptionsImportRow.batch_id == b.id)).all():
            out[s] = None
    from app.models import InsiderTransaction

    since = rs.today_ny() - timedelta(days=90)
    for s in db.scalars(select(InsiderTransaction.ticker).where(InsiderTransaction.code == "P",
                                                               InsiderTransaction.filing_date >= since).distinct()).all():
        out[s] = None
    for e in rs.upcoming_earnings(db, 7):
        out[e.symbol] = None
    return [s for s in out if s and len(s) <= 16]


def _names(db: Session, syms: set[str]) -> dict[str, str]:
    if not syms:
        return {}
    return dict(db.execute(select(Security.ticker, Security.name).where(Security.ticker.in_(syms))).all())


def analyst_rows(db: Session, min_strong_buy: int = 1) -> dict:
    from app.services.research import shariah_map

    snaps = db.scalars(select(AnalystSnapshot).where(AnalystSnapshot.strong_buy >= min_strong_buy)).all()
    syms = {a.symbol for a in snaps}
    names, sh = _names(db, syms), shariah_map(db, syms)
    rows = []
    for a in snaps:
        total = sum(x or 0 for x in (a.strong_buy, a.buy, a.hold, a.sell, a.strong_sell))
        rows.append({"symbol": a.symbol, "name": names.get(a.symbol), "strong_buy": a.strong_buy, "buy": a.buy,
                     "hold": a.hold, "sell": a.sell, "strong_sell": a.strong_sell, "total": total,
                     "strong_buy_pct": (a.strong_buy or 0) / total * 100 if total else None,
                     "buy_pct": ((a.strong_buy or 0) + (a.buy or 0)) / total * 100 if total else None,
                     "provider": a.provider, "retrieved_at": _aware(a.retrieved_at).isoformat(),
                     "shariah_status": sh.get(a.symbol)})
    rows.sort(key=lambda r: (-(r["strong_buy"] or 0), r["symbol"]))
    uni = universe(db)
    have = set(db.scalars(select(AnalystSnapshot.symbol).where(AnalystSnapshot.symbol.in_(uni))).all()) if uni else set()
    return {"rows": rows, "universe": len(uni), "missing": len([s for s in uni if s not in have])}


async def load_analysts(db: Session, ds, limit: int = 100) -> dict:
    from app.services.research import load_analyst_counts

    return await load_analyst_counts(db, ds, universe(db), limit)


def sentisense_rows(db: Session) -> dict:
    from app.services.research import shariah_map

    rows = db.scalars(select(SentiSenseRatingRow)).all()
    syms = {r.symbol for r in rows}
    names, sh = _names(db, syms), shariah_map(db, syms)
    out = [{"symbol": r.symbol, "name": r.company or names.get(r.symbol), "rated": r.rated, "letter": r.letter,
            "score": r.score, "percentile": r.percentile, "penalty_points": r.penalty_points,
            "analyst_favorable_pct": r.analyst_favorable_pct, "as_of": r.as_of.isoformat() if r.as_of else None,
            "reason": r.reason, "source": r.source, "shariah_status": sh.get(r.symbol)} for r in rows]
    out.sort(key=lambda r: (not r["rated"], -(r["score"] or -1), r["symbol"]))
    last = db.scalar(select(func.max(SentiSenseRatingRow.retrieved_at)))
    src = db.scalars(select(SentiSenseRatingRow.source).order_by(SentiSenseRatingRow.retrieved_at.desc()).limit(1)).first()
    return {"rows": out, "retrieved_at": _aware(last).isoformat() if last else None, "source": src}


def _store(db: Session, sym: str, r: dict, source: str) -> None:
    as_of = None
    try:
        as_of = date.fromisoformat(str(r.get("as_of"))[:10]) if r.get("as_of") else None
    except ValueError:
        pass
    db.merge(SentiSenseRatingRow(symbol=sym[:16], rated=bool(r.get("rated")), letter=(r.get("letter") or None),
                                 score=r.get("score"), percentile=r.get("percentile"),
                                 penalty_points=r.get("penalty_points"),
                                 analyst_favorable_pct=r.get("analyst_favorable_pct"),
                                 company=(r.get("company") or None), as_of=as_of, reason=r.get("reason"),
                                 source=source, retrieved_at=_now()))


async def load_sentisense(db: Session, ds, per_stock_limit: int = 25) -> dict:
    """Screener first (one request for every rated stock, at most once per ~day); otherwise per stock."""
    p = getattr(ds, "sentisense", None)
    if p is None:
        return {"fetched": 0, "remaining": 0, "mode": None, "errors": ["SENTISENSE_API_KEY is not set"]}
    errors: list[str] = []
    last_screen = _aware(db.scalar(select(func.max(SentiSenseRatingRow.retrieved_at))
                                   .where(SentiSenseRatingRow.source == "screener")))
    last_single = _aware(db.scalar(select(func.max(SentiSenseRatingRow.retrieved_at))
                                   .where(SentiSenseRatingRow.source == "rating")))
    screener_failed_recently = last_single is not None and _now() - last_single < TTL and not last_screen
    if not screener_failed_recently and (not last_screen or _now() - last_screen > TTL):
        try:
            rows = await p.screener()
            if rows:
                db.execute(delete(SentiSenseRatingRow).where(SentiSenseRatingRow.source == "screener"))
                for r in rows:
                    _store(db, r["ticker"], r | {"as_of": date.today().isoformat()}, "screener")
                db.commit()
                return {"fetched": len(rows), "remaining": 0, "mode": "screener", "errors": []}
            errors.append("screener returned no rows")
        except Exception as e:  # noqa: BLE001 - fall back to per-stock ratings
            errors.append(str(e)[:200])
    elif last_screen and _now() - last_screen <= TTL:
        return {"fetched": 0, "remaining": 0, "mode": "screener", "errors": []}
    uni = universe(db)
    have = {r.symbol: r for r in db.scalars(select(SentiSenseRatingRow).where(SentiSenseRatingRow.symbol.in_(uni))).all()} if uni else {}
    todo = [s for s in uni if s not in have or _now() - _aware(have[s].retrieved_at) > TTL]
    done = 0
    for s in todo[:per_stock_limit]:
        try:
            r = await p.rating(s)
        except ProviderError as e:
            errors.append(f"{s}: {e}"[:200])
            if e.status in (401, 403, 429):
                break
            continue
        _store(db, s, r or {"rated": False, "reason": "not_covered"}, "rating")
        done += 1
    db.commit()
    return {"fetched": done, "remaining": max(0, len(todo) - done), "mode": "per-stock", "errors": errors[:3]}
