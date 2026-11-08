"""FastAPI application. API keys never leave the backend; the frontend only talks to these routes."""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, ProgrammingError
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import market_calendar as mc
from app.auth import SESSION_COOKIE, admin_user, create_token, current_user, hash_password, verify_password
from app.config import ScreeningConfig, get_settings
from app.db import SessionLocal, get_db
from app.models import (AppSettings, BusinessReviewRow, DailyBar, InsiderTransaction, JournalDividend, JournalFill,
                        EarningsEvent, OptionsImportBatch, OptionsImportRow, ProviderHealth, Recommendation, RecommendationRevision,
                        ScanResult, ScanRun, Security, User)
from app.screening.shariah import purification
from app.services.engine import ScanEngine, load_config
from app.tools.expectancy import ExpectancyInput, calculate_expectancy, scenario_grid
from app.tools.pnl import Dividend, Fill, summarize
from app.tools.position import PositionInput, calculate_position

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("app")
MIGRATION_ERROR: str | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global MIGRATION_ERROR
    if get_settings().auto_migrate:
        try:
            from app.migrate import upgrade_head

            await asyncio.to_thread(upgrade_head)
        except Exception as e:  # keep serving so /api/health can report the problem
            MIGRATION_ERROR = repr(e)
            log.exception("database migration failed")
    yield


app = FastAPI(title="Stock Intel API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins.split(","), allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(OperationalError)
@app.exception_handler(ProgrammingError)
async def database_unavailable(_request, exc):
    """Show a clear reason instead of a bare 500 when the database is missing or not migrated."""
    log.error("database error: %s", exc)
    if isinstance(exc, ProgrammingError) and "does not exist" not in str(exc):
        return JSONResponse({"detail": "Database error. Check the server logs."}, status_code=500)
    return JSONResponse(
        {"detail": "The database is not connected or not set up yet. Connect a PostgreSQL database "
                   "(e.g. Neon in Vercel → Storage) and redeploy. See /api/health for details."},
        status_code=503)

DISCLAIMER = ("Research tool only — not investment advice. Screening results depend on third-party data that may be "
              "delayed or incomplete. Shariah highlighting (green / red) is an analytical aid, not a fatwa.")


def make_datasource():  # overridden in tests
    from app.services.datasource import LiveDataSource

    return LiveDataSource()


# ------------------------------------------------------------------ auth
class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)


def _session_response(u: User, response: Response) -> dict:
    """Browser sessions use an httpOnly cookie (not readable by page scripts). The token is also
    returned in the body for non-browser API clients that send it as a Bearer header."""
    s = get_settings()
    token = create_token(u)
    response.set_cookie(SESSION_COOKIE, token, max_age=s.jwt_ttl_minutes * 60, httponly=True,
                        secure=s.app_env == "production", samesite="lax", path="/")
    return {"token": token, "role": u.role}


@app.post("/api/auth/register")
def register(c: Credentials, response: Response, db: Session = Depends(get_db)):
    if db.scalars(select(User).where(User.email == c.email.lower())).first():
        raise HTTPException(409, "email already registered")
    first = db.scalar(select(func.count(User.id))) == 0
    u = User(email=c.email.lower(), password_hash=hash_password(c.password), role="admin" if first else "user")
    db.add(u)
    db.commit()
    return _session_response(u, response)


@app.post("/api/auth/login")
def login(c: Credentials, response: Response, db: Session = Depends(get_db)):
    u = db.scalars(select(User).where(User.email == c.email.lower())).first()
    if not u or not verify_password(c.password, u.password_hash):
        raise HTTPException(401, "invalid credentials")
    return _session_response(u, response)


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


class NotifyPrefs(BaseModel):
    notify_email: bool = False
    notify_telegram: bool = False
    telegram_chat_id: str | None = None


@app.get("/api/me")
def me(u: User = Depends(current_user)):
    return {"id": u.id, "email": u.email, "role": u.role, "notify_email": u.notify_email,
            "notify_telegram": u.notify_telegram, "telegram_chat_id": u.telegram_chat_id}


@app.put("/api/me/notifications")
def set_notify(p: NotifyPrefs, u: User = Depends(current_user), db: Session = Depends(get_db)):
    u.notify_email, u.notify_telegram, u.telegram_chat_id = p.notify_email, p.notify_telegram, p.telegram_chat_id
    db.merge(u)
    db.commit()
    return me(u)


# ------------------------------------------------------------------ helpers
def _latest_run(db: Session, session_date: date | None = None) -> ScanRun | None:
    q = select(ScanRun).where(ScanRun.status.in_(("ok", "partial")), ScanRun.kind != "backtest")
    if session_date:
        q = q.where(ScanRun.session_date == session_date)
    return db.scalars(q.order_by(ScanRun.id.desc()).limit(1)).first()


def _run_dict(r: ScanRun | None) -> dict | None:
    if not r:
        return None
    return {"id": r.id, "kind": r.kind, "session_date": r.session_date.isoformat(), "status": r.status,
            "started_at": r.started_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "funnel": r.funnel, "errors": r.errors, "relaxed_criteria": r.relaxed_criteria,
            "triggered_by": r.triggered_by}


def _effective(db: Session, rec: Recommendation) -> dict:
    revs = db.scalars(select(RecommendationRevision).where(RecommendationRevision.recommendation_id == rec.id)
                      .order_by(RecommendationRevision.id)).all()
    base = {"id": rec.id, "session_date": rec.session_date.isoformat(), "run_id": rec.run_id, "kind": rec.kind,
            "is_final": rec.is_final, "rank": rec.rank, "ticker": rec.ticker, "score": rec.score,
            "price_at_rec": rec.price_at_rec, "entry": rec.entry, "stop": rec.stop, "target1": rec.target1,
            "target2": rec.target2, "created_at": rec.created_at.isoformat(), **rec.payload}
    for rv in revs:
        base |= rv.after
    base["revisions"] = [{"changed_at": r.changed_at.isoformat(), "changed_by": r.changed_by, "reason": r.reason,
                          "before": r.before, "after": r.after} for r in revs]
    return base


def _result_dict(r: ScanResult) -> dict:
    return {"ticker": r.ticker, "company": r.company, "eligible": r.eligible, "score": r.score, "price": r.price,
            "shariah_status": r.shariah_status, "failures": r.failures, "criteria": r.criteria, "extra": r.extra}


def _legacy_status(st: str | None) -> str | None:
    """Colour status (Compliant / Non-Compliant / Needs Review) from an engine status; older stored
    scan results already hold the colour status."""
    from app.screening.shariah_engine import LEGACY

    return LEGACY.get(st, st) if st else None


# ------------------------------------------------------------------ dashboard
def shariah_statuses(db: Session, tickers: set[str]) -> dict[str, str]:
    """Latest Shariah status per ticker — used only to colour tickers (green / red), never to filter."""
    from app.services import research as rs

    return rs.shariah_map(db, set(tickers))


def latest_insider_buys(db: Session, days: int, limit: int, hide_non_compliant: bool = True,
                        raw_rows: bool = False) -> list[dict]:
    """Open-market purchases (Form 4 code P) filed in the last ``days``; joint/indirect reports of one
    purchase are shown once, with the co-reporters listed."""
    since = date.today() - timedelta(days=days)
    buys = db.scalars(select(InsiderTransaction).where(InsiderTransaction.code == "P", InsiderTransaction.acquired_disposed == "A",
                                                       InsiderTransaction.is_derivative.is_(False),
                                                       InsiderTransaction.filing_date >= since)
                      .order_by(InsiderTransaction.filing_date.desc(), InsiderTransaction.direct_indirect)
                      .limit(limit if raw_rows else limit * 8)).all()
    seen: dict[tuple, dict] = {}
    for b in buys:
        key = (b.issuer_cik, b.transaction_date, round(b.shares, 4), round(b.price or 0, 4))
        if key in seen:
            if b.owner_name not in seen[key]["co_reporters"] and b.owner_name != seen[key]["owner"]:
                seen[key]["co_reporters"].append(b.owner_name)
            continue
        seen[key] = {"ticker": b.ticker, "owner": b.owner_name, "co_reporters": [],
                     "title": b.officer_title or ("Director" if b.is_director else "10% Owner" if b.is_ten_pct_owner else ""),
                     "transaction_date": b.transaction_date.isoformat(), "filing_date": b.filing_date.isoformat(),
                     "shares": b.shares, "price": b.price, "value": (b.price or 0) * b.shares,
                     "owned_after": b.shares_owned_after, "accession": b.accession}
    from app.services.research import shares_map

    rows = list(seen.values()) if raw_rows else list(seen.values())[:limit]
    from app.services.research import NON_COMPLIANT

    status = shariah_statuses(db, {r["ticker"] for r in rows})
    if hide_non_compliant:  # scan results hide non-compliant stocks
        rows = [r for r in rows if status.get(r["ticker"]) != NON_COMPLIANT]
    so = shares_map(db, {r["ticker"] for r in rows})
    for r in rows:
        r["shariah_status"] = status.get(r["ticker"])
        n = so.get(r["ticker"])
        r["outstanding_shares"] = n
        r["pct_outstanding"] = r["shares"] / n * 100 if n else None
    return rows


@app.get("/api/diagnostics")
async def diagnostics(earnings: bool = False, _: User = Depends(current_user)):
    """One small request to every configured data provider: status, time and a sample value."""
    import time as _t

    from app.providers.market import fmp_quote
    from app.providers.openinsider import OpenInsider

    ds = make_datasource()
    day = mc.last_completed_session(datetime.now(timezone.utc))

    async def check(name, coro_fn):
        t0 = _t.time()
        try:
            detail = await asyncio.wait_for(coro_fn(), 25)
            return {"provider": name, "ok": True, "ms": int((_t.time() - t0) * 1000), "detail": detail}
        except Exception as e:  # noqa: BLE001 - report every failure
            return {"provider": name, "ok": False, "ms": int((_t.time() - t0) * 1000),
                    "detail": f"{type(e).__name__}: {str(e)[:300]}"}

    jobs = []
    if ds.alpaca:
        jobs.append(check("alpaca (price)", lambda: _price(ds.alpaca.snapshot("AAPL"))))

        async def alpaca_hist():
            bars = await ds.alpaca.multi_daily_bars(["AAPL", "MSFT", "F"], day - timedelta(days=14), day)
            return {"feed": getattr(ds.alpaca, "last_feed", None), "bars": {k: len(v) for k, v in bars.items()},
                    "last": max((v[-1][0] for v in bars.values() if v), default=None)}

        jobs.append(check("alpaca (multi-stock bars)", alpaca_hist))
    if ds.finnhub:
        jobs.append(check("finnhub (analysts)", lambda: ds.finnhub.recommendation("AAPL")))
    if ds.tiingo:
        jobs.append(check("tiingo (history)", lambda: _rows(ds.tiingo.daily_bars("AAPL", day - timedelta(days=10), day))))
    if ds.fmp:
        jobs.append(check("fmp (fundamentals)", lambda: _price(fmp_quote(ds.fmp, "AAPL"))))
    if ds.massive:
        jobs.append(check("massive (whole-market day)", lambda: _count(ds.massive.grouped_daily(day))))
    if ds.sentisense:
        jobs.append(check("sentisense (rating)", lambda: ds.sentisense.rating("AAPL")))
    if ds.sec:
        jobs.append(check("sec_edgar (company list)", lambda: _count(ds.sec.universe())))
    jobs.append(check("openinsider (screener)", lambda: _count(OpenInsider().purchases())))
    if earnings:
        from collections import Counter

        from app.providers.market import fmp_earnings_calendar
        from app.services.research import today_ny

        t0 = today_ny()
        t1 = t0 + timedelta(days=7)

        async def by_day(coro):
            rows = await coro
            return dict(sorted(Counter(r["report_date"] for r in rows).items()))

        if ds.fmp:
            jobs.append(check("earnings: fmp", lambda: by_day(fmp_earnings_calendar(ds.fmp, t0, t1))))
        if ds.finnhub:
            jobs.append(check("earnings: finnhub", lambda: by_day(ds.finnhub.earnings_calendar(t0, t1))))
        if ds.av:
            async def av_rows():
                return [r for r in await ds.av.earnings_calendar() if t0.isoformat() <= r["report_date"] <= t1.isoformat()]
            jobs.append(check("earnings: alphavantage", lambda: by_day(av_rows())))
    results = await asyncio.gather(*jobs)
    return {"session": day.isoformat(), "results": results, "configured": ds.describe()}


async def _price(coro):
    q = await coro
    return {k: q.get(k) for k in ("price", "pe", "market_cap") if k in q}


async def _rows(coro):
    df = await coro
    return {"rows": len(df), "last": str(df.index[-1].date()) if len(df) else None}


async def _count(coro):
    return {"items": len(await coro)}


@app.get("/api/insiders/source-check")
async def insiders_source_check(_: User = Depends(current_user)):
    """Fetch and parse the OpenInsider screener without storing anything (diagnostics)."""
    from app.providers.openinsider import OpenInsider

    p = OpenInsider()
    try:
        txns = await p.purchases()
        return {"ok": True, "purchases": len(txns), "fetch": p.last,
                "sample": [{"ticker": t.ticker, "insider": t.owner_name, "title": t.officer_title,
                            "trade_date": t.transaction_date.isoformat(), "filing_date": t.filing_date.isoformat(),
                            "shares": t.shares, "price": t.price, "accession": t.accession} for t in txns[:3]]}
    except Exception as e:  # noqa: BLE001 - diagnostics endpoint reports any failure
        return {"ok": False, "error": str(e), "fetch": p.last}


OPENINSIDER_URL = ("http://openinsider.com/screener?s=&o=&pl=&ph=&ll=&lh=&fd=730&fdr=&td=0&tdr=&fdlyl=&fdlyh=&daysago="
                   "&xp=1&vl=&vh=&ocl=&och=&sic1=-1&sicl=100&sich=9999&grp=0&nfl=&nfh=&nil=&nih=&nol=&noh=&v2l=&v2h="
                   "&oc2l=&oc2h=&sortcol=0&cnt=100&page=1")
OPENINSIDER_MAX_AGE_HOURS = 6


async def _refresh_openinsider(db: Session) -> str | None:
    """Pull the screener's top 100 results (latest purchase filings) if the last pull is older than 6 hours."""
    from app.models import ProviderHealth
    from app.services.engine import ScanEngine

    if get_settings().insider_source != "openinsider":
        return None
    h = db.get(ProviderHealth, "openinsider")
    last = h.last_success_at if h else None
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if last and datetime.now(timezone.utc) - last < timedelta(hours=OPENINSIDER_MAX_AGE_HOURS):
        return None
    try:
        ok = await ScanEngine(db, make_datasource())._ingest_openinsider()
        return None if ok else "OpenInsider did not respond; showing the last pulled results"
    except Exception as e:  # noqa: BLE001 - keep showing stored results
        return f"OpenInsider refresh failed: {str(e)[:150]}"


@app.get("/api/insiders")
async def insiders(top: int = 100, min_pct: float = 0.0, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """The OpenInsider screener's top results (latest open-market purchases, newest filing first), refreshed
    at most every 6 hours. ``min_pct`` > 0 keeps purchases above that % of shares outstanding."""
    from app.services.research import NON_COMPLIANT

    refresh_error = await _refresh_openinsider(db)
    top = min(max(top, 1), 500)
    # the latest ``top`` screener rows (one row per filer), then joint filings merged and non-compliant hidden
    every = latest_insider_buys(db, 730, top, hide_non_compliant=False, raw_rows=True)
    rows = [r for r in every if r["shariah_status"] != NON_COMPLIANT]
    pulled = min(top, db.scalar(select(func.count()).select_from(InsiderTransaction)
                                .where(InsiderTransaction.code == "P", InsiderTransaction.acquired_disposed == "A",
                                       InsiderTransaction.is_derivative.is_(False),
                                       InsiderTransaction.filing_date >= date.today() - timedelta(days=730))) or 0)
    missing = sorted({r["ticker"] for r in rows if r["outstanding_shares"] is None})
    shown = rows if min_pct <= 0 else [r for r in rows if r["pct_outstanding"] is not None and r["pct_outstanding"] > min_pct]
    last = db.scalar(select(func.max(InsiderTransaction.filing_date)))
    from app.services.research import shares_map

    unknown = [t for t in missing if t not in shares_map(db, set(missing))]
    from app.models import ProviderHealth

    h = db.get(ProviderHealth, "openinsider")
    return {"top": top, "min_pct": min_pct, "rows": shown, "total_purchases": len(rows), "url": OPENINSIDER_URL,
            "screener_rows": pulled, "hidden_non_compliant": len(every) - len(rows),
            "merged_joint": max(0, pulled - len(every)),
            "pulled_at": h.last_success_at.isoformat() if h and h.last_success_at else None,
            "refresh_error": refresh_error,
            "shares_missing": len(unknown), "shares_unavailable": len(missing) - len(unknown),
            "latest_filing_date": last.isoformat() if last else None,
            "source": ("OpenInsider screener (SEC Form 4 open-market purchases, code P)"
                       if get_settings().insider_source == "openinsider" else "SEC EDGAR Form 4 (open-market purchases, code P)")}


@app.post("/api/insiders/load-shares")
async def insiders_load_shares(top: int = 100, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Look up shares outstanding for tickers on the Insider Buying list (cached 30 days)."""
    from app.services.research import load_shares

    rows = latest_insider_buys(db, 730, min(max(top, 1), 500), raw_rows=True)
    return await load_shares(db, make_datasource(), [r["ticker"] for r in rows if r["outstanding_shares"] is None])


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db), _: User = Depends(current_user)):
    run = _latest_run(db)
    now = datetime.now(timezone.utc)
    out: dict = {"disclaimer": DISCLAIMER, "market_phase": mc.market_phase(now), "now": now.isoformat(),
                 "latest_run": _run_dict(run), "top": [], "watchlist": [], "unusual_options": [], "insider_buys": []}
    if run:
        recs = db.scalars(select(Recommendation).where(Recommendation.run_id == run.id).order_by(Recommendation.rank)).all()
        from app.services.research import hide_non_compliant

        out["top"] = hide_non_compliant(db, [_effective(db, r) for r in recs], "ticker")
        results = db.scalars(select(ScanResult).where(ScanResult.run_id == run.id)).all()
        out["watchlist"] = hide_non_compliant(db, [_result_dict(r) for r in results if (r.extra or {}).get("watchlist")],
                                              "ticker")
        opt = [r for r in results if r.criteria.get("options", {}).get("outcome") == "pass"]
        out["unusual_options"] = [{"ticker": r.ticker, "company": r.company, "shariah_status": r.shariah_status,
                                   **{k: r.criteria["options"]["metrics"].get(k) for k in (
            "current_volume", "baseline_volume", "volume_change_pct", "iv_rank", "put_call_ratio", "call_volume", "put_volume",
            "current_session")}} for r in sorted(opt, key=lambda r: -(r.criteria["options"]["metrics"].get("volume_change_pct") or 0))]
        out["unusual_options"] = hide_non_compliant(db, out["unusual_options"], "ticker")[:20]
    out["insider_buys"] = latest_insider_buys(db, 7, 25)
    out["options_source"] = _options_source(db)
    return out


def _options_source(db: Session) -> dict:
    cfg = load_config(db)
    if cfg.options.source != "csv":
        return {"type": cfg.options.source}
    b = db.scalars(select(OptionsImportBatch).order_by(OptionsImportBatch.session_date.desc(),
                                                       OptionsImportBatch.id.desc()).limit(1)).first()
    last = mc.last_completed_session(datetime.now(timezone.utc))
    return {"type": "csv", "latest": _batch_dict(b) if b else None,
            "current": bool(b and b.session_date >= last), "last_completed_session": last.isoformat()}


def health_rows(db: Session) -> list[dict]:
    rows = db.scalars(select(ProviderHealth)).all()
    now = datetime.now(timezone.utc)
    out = []
    for h in rows:
        last = h.last_success_at.replace(tzinfo=h.last_success_at.tzinfo or timezone.utc) if h.last_success_at else None
        age = (now - last).total_seconds() / 3600 if last else None
        out.append({"provider": h.provider, "last_success_at": last.isoformat() if last else None,
                    "last_error_at": h.last_error_at.isoformat() if h.last_error_at else None, "last_error": h.last_error,
                    "calls": h.calls, "failures": h.failures,
                    "state": "unknown" if age is None else "fresh" if age < 1 else "stale" if age < 24 else "down"})
    return out


@app.get("/api/health")
def health(db: Session = Depends(get_db)):
    ds = make_datasource()
    out = {"providers_configured": ds.describe(), "config_errors": getattr(ds, "errors", []),
           "market_phase": mc.market_phase(datetime.now(timezone.utc)), "migration_error": MIGRATION_ERROR}
    try:
        db.execute(select(1))
        src = _options_source(db)
        if src["type"] == "csv":  # ORATS is optional when options data comes from CSV imports
            out["providers_configured"] = out["providers_configured"] | {"orats": "not needed (CSV import mode)"}
            out["config_errors"] = [e for e in out["config_errors"] if "[orats]" not in e]
        return out | {"db": "ok", "provider_health": health_rows(db), "options_source": src}
    except Exception as e:
        return out | {"db": f"error: {type(e).__name__}: {str(e)[:300]}", "provider_health": []}


# ------------------------------------------------------------------ recommendations
@app.get("/api/recommendations")
def recommendations(session_date: date | None = None, db: Session = Depends(get_db), _: User = Depends(current_user)):
    run = _latest_run(db, session_date)
    if not run:
        return {"run": None, "top": [], "message": "No completed scan for this date."}
    recs = db.scalars(select(Recommendation).where(Recommendation.run_id == run.id).order_by(Recommendation.rank)).all()
    msg = None if recs else "No stocks met all mandatory requirements in this scan."
    from app.services.research import hide_non_compliant

    return {"run": _run_dict(run), "top": hide_non_compliant(db, [_effective(db, r) for r in recs], "ticker"), "message": msg}


@app.get("/api/recommendations/history")
def rec_history(days: int = 90, db: Session = Depends(get_db), _: User = Depends(current_user)):
    since = date.today() - timedelta(days=days)
    recs = db.scalars(select(Recommendation).where(Recommendation.session_date >= since, Recommendation.is_final.is_(True))
                      .order_by(Recommendation.session_date.desc(), Recommendation.rank)).all()
    out = []
    for r in recs:
        e = _effective(db, r)
        last = db.scalars(select(DailyBar).where(DailyBar.ticker == r.ticker).order_by(DailyBar.session.desc()).limit(1)).first()
        after = db.scalars(select(DailyBar).where(DailyBar.ticker == r.ticker, DailyBar.session > r.session_date)).all()
        hit_stop = any(b.low <= (e.get("stop") or -1) for b in after)
        hit_t1 = any(b.high >= (e.get("target1") or 1e18) for b in after)
        out.append({k: e[k] for k in ("id", "session_date", "rank", "ticker", "score", "price_at_rec", "entry", "stop",
                                       "target1", "target2", "revisions")} |
                   {"last_price": last.close if last else None, "last_session": last.session.isoformat() if last else None,
                    "return_pct": (last.close / r.price_at_rec - 1) * 100 if last and r.price_at_rec else None,
                    "hit_stop": hit_stop, "hit_target1": hit_t1,
                    "shariah_status": _legacy_status(((e.get("extra") or {}).get("shariah") or {}).get("status"))})
    return out


class Revision(BaseModel):
    reason: str = Field(min_length=5)
    changes: dict


@app.post("/api/recommendations/{rec_id}/revise")
def revise(rec_id: int, rv: Revision, db: Session = Depends(get_db), u: User = Depends(admin_user)):
    rec = db.get(Recommendation, rec_id)
    if not rec:
        raise HTTPException(404)
    allowed = {"entry", "stop", "target1", "target2", "note"}
    if not set(rv.changes) <= allowed:
        raise HTTPException(422, f"only {sorted(allowed)} may be revised")
    cur = _effective(db, rec)
    db.add(RecommendationRevision(recommendation_id=rec_id, changed_by=u.email, reason=rv.reason,
                                  before={k: cur.get(k) for k in rv.changes}, after=rv.changes))
    db.commit()
    return _effective(db, rec)


# ------------------------------------------------------------------ stock detail
@app.get("/api/stocks/{ticker}")
async def stock(ticker: str, db: Session = Depends(get_db), _: User = Depends(current_user)):
    ticker = ticker.upper()
    res = db.scalars(select(ScanResult).join(ScanRun).where(ScanResult.ticker == ticker)
                     .order_by(ScanResult.id.desc()).limit(1)).first()
    sec = db.get(Security, ticker)
    bars = db.scalars(select(DailyBar).where(DailyBar.ticker == ticker).order_by(DailyBar.session.desc()).limit(400)).all()[::-1]
    out = {"ticker": ticker, "security": {"name": sec.name, "cik": sec.cik, "exchange": sec.exchange} if sec else None,
           "result": _result_dict(res) if res else None, "run_id": res.run_id if res else None,
           "bars": [{"time": b.session.isoformat(), "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                     "volume": b.volume} for b in bars],
           "bars_provider": bars[-1].provider if bars else None, "disclaimer": DISCLAIMER}
    return out


@app.get("/api/stocks/{ticker}/external")
async def stock_external(ticker: str, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """News and SEC filings, fetched live with a short timeout (loaded lazily by the UI)."""
    ticker = ticker.upper()
    sec = db.get(Security, ticker)
    out: dict = {"news": [], "filings": []}
    ds = make_datasource()
    try:
        out["news"], prov = await asyncio.wait_for(ds.news(ticker), 10)
        out["news_provenance"] = prov.to_dict()
    except Exception as e:
        out["news_error"] = str(e) or type(e).__name__
    if not (sec and sec.cik and getattr(ds, "sec", None)):
        out["filings_error"] = "no SEC CIK for this ticker" if not (sec and sec.cik) else "SEC EDGAR not configured"
        return out
    try:
        sub = await asyncio.wait_for(ds.sec.submissions(sec.cik), 10)
        r = sub.get("filings", {}).get("recent", {})
        out["filings"] = [{"form": f, "date": d, "accession": a,
                           "url": f"https://www.sec.gov/Archives/edgar/data/{int(sec.cik)}/{a.replace('-', '')}/{p}"}
                          for f, d, a, p in list(zip(r.get("form", []), r.get("filingDate", []),
                                                     r.get("accessionNumber", []), r.get("primaryDocument", [])))[:20]]
        out["company_profile"] = {"sic": sub.get("sic"), "sic_description": sub.get("sicDescription"),
                                  "state": sub.get("stateOfIncorporation"), "website": sub.get("website")}
    except Exception as e:
        out["filings_error"] = str(e) or "timed out"
    return out


# ------------------------------------------------------------------ scanning
_scan_lock = asyncio.Lock()
STALE_RUN_MINUTES = 20  # a "running" scan older than this is treated as crashed


def _scan_running(db: Session) -> bool:
    """Checked in the database so it also works across serverless instances."""
    # Vercel stops a function after 300 s, so a run "running" for over 6 minutes there has died
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=6 if _serverless() else STALE_RUN_MINUTES)
    # runs cut off by the serverless time limit never finish: mark them failed so they don't look active
    stale = db.scalars(select(ScanRun).where(ScanRun.status == "running", ScanRun.started_at < cutoff)).all()
    for r in stale:
        r.status, r.finished_at = "failed", datetime.now(timezone.utc)
        r.errors = (r.errors or []) + ["scan did not finish (time limit reached); later scans continue from where it stopped"]
    if stale:
        db.commit()
    return db.scalars(select(ScanRun.id).where(ScanRun.status == "running", ScanRun.started_at >= cutoff)
                      .limit(1)).first() is not None


async def _do_scan(kind: str, by: str) -> dict:
    async with _scan_lock:
        with SessionLocal() as db:
            ds = make_datasource()
            eng = ScanEngine(db, ds)
            eng.errors.extend(getattr(ds, "errors", []))
            run = await eng.run(kind=kind, triggered_by=by)
            from app.services.research import refresh_earnings

            await refresh_earnings(db, ds)
            from app.services.research import load_shares

            await load_shares(db, ds, [r["ticker"] for r in latest_insider_buys(db, 90, 500)
                                       if r["outstanding_shares"] is None], limit=80, max_seconds=40)
            from app.services import shariah as shs

            try:  # Shariah engine: refresh SEC figures weekly (continues on the Shariah page if not finished)
                if shs.status(db)["results"] and shs.is_stale(db, 7):
                    await shs.load(db, ds, max_seconds=60, refresh=True)
            except Exception as e:  # noqa: BLE001
                log.warning("shariah refresh skipped: %s", e)
            from app.services import seasonality as ss

            try:  # Seasonality: add the newest completed month once the API history is loaded
                st = ss.api_status(db)
                if st["loaded"] and st["stale"]:
                    await ss.load_api(db, ds, max_seconds=60)
            except Exception as e:  # noqa: BLE001
                log.warning("seasonality refresh skipped: %s", e)
            from app.services import ratings

            try:  # Ratings tab: SentiSense screener (once a day) and analyst counts for the followed stocks
                await asyncio.wait_for(ratings.load_sentisense(db, ds), 30)
                await asyncio.wait_for(ratings.load_analysts(db, ds, 50), 50)
            except Exception as e:  # noqa: BLE001 - ratings never fail the scan
                log.warning("ratings refresh skipped: %s", e)
            prior = set(db.scalars(select(Recommendation.ticker).where(
                Recommendation.session_date == run.session_date, Recommendation.run_id != run.id)).all())
            new = [r for r in db.scalars(select(Recommendation).where(Recommendation.run_id == run.id)).all()
                   if r.ticker not in prior]
            if new:
                from app.services.notify import notify_new

                notify_new(db, new)
            return {"run_id": run.id, "status": run.status, "kind": kind, "new": [r.ticker for r in new]}


def _serverless() -> bool:
    return bool(os.environ.get("VERCEL"))


@app.post("/api/scan/daily")
async def daily_scan(bg: BackgroundTasks, u: User = Depends(current_user), db: Session = Depends(get_db)):
    """Run a scan when the app is first opened on a new (New York) day; otherwise do nothing."""
    today = datetime.now(mc.NY).date()
    last = db.scalars(select(ScanRun).order_by(ScanRun.started_at.desc()).limit(1)).first()
    if last and last.started_at:
        started = last.started_at if last.started_at.tzinfo else last.started_at.replace(tzinfo=timezone.utc)
        if started.astimezone(mc.NY).date() == today:
            return {"ran": False, "reason": "already scanned today", "last_run": last.id}
    if _scan_lock.locked() or _scan_running(db):
        return {"ran": False, "reason": "a scan is already running"}
    if _serverless():
        return {"ran": True, "queued": False, **await _do_scan("manual", f"auto (first open {today.isoformat()})")}
    bg.add_task(_do_scan, "manual", f"auto (first open {today.isoformat()})")
    return {"ran": True, "queued": True}


@app.post("/api/scan")
async def manual_scan(bg: BackgroundTasks, u: User = Depends(current_user), db: Session = Depends(get_db)):
    if _scan_lock.locked() or _scan_running(db):
        raise HTTPException(409, "a scan is already running")
    if _serverless():
        # Serverless instances may be frozen after the response, so run inside the request.
        return {"queued": False, **await _do_scan("manual", u.email)}
    bg.add_task(_do_scan, "manual", u.email)
    return {"queued": True}


def cron_kind(now: datetime) -> str | None:
    """Which scheduled scan (if any) belongs to this moment, in New York time.
    Vercel Cron schedules are UTC; the cron fires every 30 minutes and this function maps the
    time to a scan, so daylight-saving changes, holidays and early closes are handled here."""
    local = now.astimezone(mc.NY)
    times = mc.session_times(local.date())
    if not times:
        return None
    open_, close = times
    t = local.replace(second=0, microsecond=0)
    if t < open_ - timedelta(minutes=90):
        return None
    if t < open_:
        return "premarket"
    if t < open_ + timedelta(minutes=30):
        return "open"
    if t < close:
        return "intraday"
    # Generous window: Vercel Hobby fires a daily cron at any minute within its scheduled hour,
    # and early-close days end at 13:00.
    if t < max(close + timedelta(minutes=90), local.replace(hour=20, minute=0, second=0, microsecond=0)):
        return "close"
    return None


@app.get("/api/cron/scan")
async def cron_scan(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    secret = get_settings().cron_secret
    if not secret or authorization != f"Bearer {secret}":
        raise HTTPException(401, "invalid cron secret")
    kind = cron_kind(datetime.now(timezone.utc))
    if kind is None:
        return {"skipped": "market closed or outside scan windows"}
    if kind != "intraday":  # premarket / open / close run once per day
        since = datetime.now(timezone.utc) - timedelta(hours=12)
        done = db.scalars(select(ScanRun.id).where(ScanRun.kind == kind, ScanRun.started_at >= since,
                                                   ScanRun.status.in_(("ok", "partial")))).first()
        if done:
            return {"skipped": f"{kind} scan already completed today"}
    if _scan_lock.locked() or _scan_running(db):
        return {"skipped": "a scan is already running"}
    return await _do_scan(kind, "vercel-cron")


@app.get("/api/scans")
def scans(limit: int = 20, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return [_run_dict(r) for r in db.scalars(select(ScanRun).order_by(ScanRun.id.desc()).limit(limit)).all()]


# ------------------------------------------------------------------ admin
@app.get("/api/admin/settings")
def get_cfg(db: Session = Depends(get_db), _: User = Depends(current_user)):
    cfg = load_config(db)
    return {"config": cfg.model_dump(by_alias=True), "relaxed": cfg.relaxed_criteria(),
            "methodologies": {m["key"]: m for m in _methods_info(db)}, "defaults": ScreeningConfig().model_dump(by_alias=True)}


class SettingsUpdate(BaseModel):
    config: dict
    note: str = Field(min_length=3)


@app.put("/api/admin/settings")
def put_cfg(s: SettingsUpdate, db: Session = Depends(get_db), u: User = Depends(admin_user)):
    try:
        cfg = ScreeningConfig.model_validate(s.config)
    except ValueError as e:
        raise HTTPException(422, str(e))
    db.add(AppSettings(config=cfg.model_dump(by_alias=True), updated_by=u.email, note=s.note))
    db.commit()
    return {"config": cfg.model_dump(by_alias=True), "relaxed": cfg.relaxed_criteria()}


@app.get("/api/admin/settings/history")
def cfg_history(db: Session = Depends(get_db), _: User = Depends(admin_user)):
    return [{"id": r.id, "updated_by": r.updated_by, "updated_at": r.updated_at.isoformat(), "note": r.note}
            for r in db.scalars(select(AppSettings).order_by(AppSettings.id.desc()).limit(100)).all()]


class ReviewIn(BaseModel):
    ticker: str
    status: str = Field(pattern="^(compliant|non_compliant)$")
    notes: str = Field(min_length=5)
    prohibited_revenue_pct: float | None = Field(default=None, ge=0, le=100)


@app.post("/api/admin/business-reviews")
def add_review(r: ReviewIn, db: Session = Depends(get_db), u: User = Depends(admin_user)):
    for old in db.scalars(select(BusinessReviewRow).where(BusinessReviewRow.ticker == r.ticker.upper())).all():
        old.superseded = True
    db.add(BusinessReviewRow(ticker=r.ticker.upper(), status=r.status, reviewer=u.email, reviewed_on=date.today(),
                             notes=r.notes, prohibited_revenue_pct=r.prohibited_revenue_pct))
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ options CSV import
class OptionsImportIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    csv: str = Field(min_length=1, max_length=4_000_000)  # Vercel request bodies are limited to ~4.5 MB
    session_date: date | None = None  # overrides the date found in the file
    percent_numbers: str = Field("auto", pattern="^(auto|fraction|percent)$")
    dry_run: bool = False


def _batch_dict(b: OptionsImportBatch) -> dict:
    return {"id": b.id, "filename": b.filename, "session_date": b.session_date.isoformat(), "rows": b.row_count,
            "percent_interpretation": b.percent_interpretation, "imported_by": b.imported_by,
            "imported_at": b.imported_at.isoformat(), "summary": b.summary}


@app.post("/api/admin/options-import")
def options_import(p: OptionsImportIn, db: Session = Depends(get_db), u: User = Depends(admin_user)):
    from app.imports.options_csv import ImportError_, parse, to_dict

    try:
        res = parse(p.csv, p.percent_numbers, p.session_date)
    except ImportError_ as e:
        raise HTTPException(422, str(e))
    session = res.session_date or datetime.now(mc.NY).date()
    summary = res.summary() | {"session_date": session.isoformat()}
    preview = [to_dict(r) for r in res.rows[:15]]
    if p.dry_run:
        return {"dry_run": True, "summary": summary, "preview": preview}
    b = OptionsImportBatch(filename=p.filename, session_date=session, row_count=len(res.rows),
                           percent_interpretation=res.percent_interpretation, summary=summary, imported_by=u.email)
    b.rows = [OptionsImportRow(symbol=r.symbol, name=r.name, price=r.price, change=r.change, pct_change=r.pct_change,
                               iv_rank=r.iv_rank, options_volume=r.options_volume,
                               vol_change_1m_pct=r.vol_change_1m_pct, put_volume=r.put_volume,
                               call_volume=r.call_volume, pc_ratio=r.pc_ratio) for r in res.rows]
    db.add(b)
    db.commit()
    return {"dry_run": False, "batch": _batch_dict(b), "summary": summary, "preview": preview}


@app.get("/api/options-imports")
def list_options_imports(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return [_batch_dict(b) for b in db.scalars(select(OptionsImportBatch).order_by(OptionsImportBatch.id.desc())
                                               .limit(30)).all()]


@app.get("/api/options-imports/{batch_id}")
def get_options_import(batch_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    b = db.get(OptionsImportBatch, batch_id)
    if not b:
        raise HTTPException(404)
    rows = sorted(b.rows, key=lambda r: -(r.vol_change_1m_pct or 0))
    status = shariah_statuses(db, {r.symbol for r in rows})
    cfg = load_config(db).options
    run = _latest_run(db)
    top = set(db.scalars(select(Recommendation.ticker).where(Recommendation.run_id == run.id)).all()) if run else set()
    max_price = load_config(db).max_price

    def passes(r) -> bool:
        return (r.price is not None and r.price < max_price and r.vol_change_1m_pct is not None
                and r.vol_change_1m_pct >= cfg.min_volume_change_pct and r.iv_rank is not None
                and r.iv_rank > cfg.min_iv_rank and r.pc_ratio is not None and r.pc_ratio < cfg.max_put_call_ratio)

    return _batch_dict(b) | {
        "thresholds": {"min_volume_change_pct": cfg.min_volume_change_pct, "min_iv_rank": cfg.min_iv_rank,
                       "max_put_call_ratio": cfg.max_put_call_ratio, "max_price": max_price},
        "data": [{c: getattr(r, c) for c in (
            "symbol", "name", "price", "change", "pct_change", "iv_rank", "options_volume", "vol_change_1m_pct",
            "put_volume", "call_volume", "pc_ratio")} | {"shariah_status": status.get(r.symbol), "passes_rules": passes(r),
                                                          "in_top5": r.symbol in top} for r in rows
                 if status.get(r.symbol) != "Non-Compliant"]}  # scan results hide non-compliant stocks


@app.delete("/api/admin/options-import/{batch_id}")
def delete_options_import(batch_id: int, db: Session = Depends(get_db), _: User = Depends(admin_user)):
    b = db.get(OptionsImportBatch, batch_id)
    if not b:
        raise HTTPException(404)
    db.delete(b)
    db.commit()
    return {"ok": True}


@app.get("/api/admin/business-reviews")
def list_reviews(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return [{"ticker": r.ticker, "status": r.status, "reviewer": r.reviewer, "reviewed_on": r.reviewed_on.isoformat(),
             "notes": r.notes, "prohibited_revenue_pct": r.prohibited_revenue_pct, "superseded": r.superseded}
            for r in db.scalars(select(BusinessReviewRow).order_by(BusinessReviewRow.id.desc())).all()]


# ------------------------------------------------------------------ tools
@app.post("/api/tools/position")
def tool_position(p: PositionInput):
    return calculate_position(p)


@app.post("/api/tools/expectancy")
def tool_expectancy(p: ExpectancyInput):
    return calculate_expectancy(p) | {"scenarios": scenario_grid(p.avg_loss or 1, cost=p.cost_per_trade)}


class PurifyIn(BaseModel):
    income: float = Field(ge=0)
    non_compliant_pct: float = Field(ge=0, le=100)


@app.post("/api/tools/purification")
def tool_purify(p: PurifyIn):
    return purification(p.income, p.non_compliant_pct)


# ------------------------------------------------------------------ journal
class FillIn(BaseModel):
    ticker: str
    side: str = Field(pattern="^(buy|sell)$")
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    fees: float = Field(0, ge=0)
    trade_date: date
    stop: float | None = None
    target: float | None = None
    notes: str | None = None
    recommendation_id: int | None = None


class DividendIn(BaseModel):
    ticker: str
    amount: float = Field(gt=0)
    pay_date: date


@app.get("/api/journal/fills")
def list_fills(db: Session = Depends(get_db), u: User = Depends(current_user)):
    return [{c: getattr(f, c) for c in ("id", "ticker", "side", "quantity", "price", "fees", "trade_date", "stop",
                                       "target", "notes", "recommendation_id")}
            for f in db.scalars(select(JournalFill).where(JournalFill.user_id == u.id).order_by(JournalFill.trade_date)).all()]


@app.post("/api/journal/fills")
def add_fill(f: FillIn, db: Session = Depends(get_db), u: User = Depends(current_user)):
    row = JournalFill(user_id=u.id, **(f.model_dump() | {"ticker": f.ticker.upper()}))
    db.add(row)
    db.commit()
    return {"id": row.id}


@app.delete("/api/journal/fills/{fid}")
def del_fill(fid: int, db: Session = Depends(get_db), u: User = Depends(current_user)):
    f = db.get(JournalFill, fid)
    if not f or f.user_id != u.id:
        raise HTTPException(404)
    db.delete(f)
    db.commit()
    return {"ok": True}


@app.post("/api/journal/dividends")
def add_div(d: DividendIn, db: Session = Depends(get_db), u: User = Depends(current_user)):
    db.add(JournalDividend(user_id=u.id, ticker=d.ticker.upper(), amount=d.amount, pay_date=d.pay_date))
    db.commit()
    return {"ok": True}


@app.get("/api/journal/summary")
def journal_summary(db: Session = Depends(get_db), u: User = Depends(current_user)):
    fills = db.scalars(select(JournalFill).where(JournalFill.user_id == u.id)).all()
    divs = db.scalars(select(JournalDividend).where(JournalDividend.user_id == u.id)).all()
    marks, mark_dates = {}, {}
    for t in {f.ticker for f in fills}:
        b = db.scalars(select(DailyBar).where(DailyBar.ticker == t).order_by(DailyBar.session.desc()).limit(1)).first()
        if b:
            marks[t], mark_dates[t] = b.close, b.session.isoformat()
    s = summarize([Fill(f.id, f.ticker, f.side, f.quantity, f.price, f.fees, f.trade_date, f.recommendation_id) for f in fills],
                  [Dividend(d.ticker, d.amount, d.pay_date) for d in divs], marks)
    # actual vs recommendation
    for c in s["closed_trades"]:
        if c["recommendation_id"] and (rec := db.get(Recommendation, c["recommendation_id"])):
            e = _effective(db, rec)
            c["recommended"] = {k: e.get(k) for k in ("entry", "stop", "target1", "target2", "session_date")}
    s["mark_dates"] = mark_dates
    s["mark_source"] = "last stored daily close (EOD)"
    return s


# ------------------------------------------------------------------ research: side panel, earnings, seasonality
@app.get("/api/stocks/{ticker}/summary")
async def stock_panel(ticker: str, db: Session = Depends(get_db), _: User = Depends(current_user)):
    from app.services.research import stock_summary

    return await stock_summary(db, make_datasource(), ticker)


@app.get("/api/earnings/markers")
async def earnings_marker_map(days: int = 7, db: Session = Depends(get_db), _: User = Depends(current_user)):
    from app.services.research import earnings_markers, refresh_earnings

    info = await refresh_earnings(db, make_datasource())  # at most once every 12 hours
    return {"days": days, "markers": earnings_markers(db, days), "refresh": info}


@app.get("/api/earnings/upcoming")
async def earnings_upcoming(days: int = 7, db: Session = Depends(get_db), _: User = Depends(current_user)):
    from app.services import research as rs

    refresh = await rs.refresh_earnings(db, make_datasource())
    events = rs.upcoming_earnings(db, min(max(days, 1), 21))
    syms = sorted({e.symbol for e in events})
    insider = rs.insider_summary(db, syms)
    batch, opts = rs.options_rows(db, syms)
    an = rs.analyst_counts(db, syms)
    sh = rs.shariah_map(db, set(syms))
    rows = []
    for e in events:
        o, a = opts.get(e.symbol), an.get(e.symbol)
        rows.append({"symbol": e.symbol, "name": e.name, "date": e.report_date.isoformat(), "time_of_day": e.time_of_day,
                     "eps_estimate": e.eps_estimate, "shariah_status": sh.get(e.symbol),
                     "insider": insider.get(e.symbol),
                     "options": None if not o else {"vol_change_1m_pct": o.vol_change_1m_pct, "options_volume": o.options_volume,
                                                    "iv_rank": o.iv_rank, "put_call": o.pc_ratio},
                     "analysts": None if not a else {"strong_buy": a.strong_buy, "buy": a.buy, "hold": a.hold,
                                                     "sell": a.sell, "strong_sell": a.strong_sell,
                                                     "retrieved_at": a.retrieved_at.isoformat()}})
    src = db.scalars(select(EarningsEvent.provider).limit(1)).first()
    rows = rs.hide_non_compliant(db, rows)  # scan results hide non-compliant stocks
    return {"days": days, "rows": rows, "refresh": refresh, "earnings_source": src,
            "options_import": {"id": batch.id, "filename": batch.filename, "session_date": batch.session_date.isoformat()} if batch else None,
            "analysts_missing": sum(1 for s in syms if s not in an), "insider_window_days": 90}


@app.post("/api/earnings/load-analysts")
async def earnings_load_analysts(days: int = 7, limit: int = 100, db: Session = Depends(get_db),
                                 _: User = Depends(current_user)):
    from app.services import research as rs

    events = rs.upcoming_earnings(db, days)
    first_date = {}
    for e in events:
        first_date.setdefault(e.symbol, e.report_date)
    syms = sorted(first_date)
    insider = rs.insider_summary(db, syms)
    _, opts = rs.options_rows(db, syms)
    # most interesting first (insider buying / unusual options), then the soonest reports
    syms.sort(key=lambda s: (s not in insider and s not in opts, first_date[s], s))
    return await rs.load_analyst_counts(db, make_datasource(), syms, min(max(limit, 1), 200))


class SeasonalityImportChunk(BaseModel):
    rows: list[list] = Field(..., max_length=100_000)  # [symbol, "YYYY-MM", close]
    replace_all: bool = False
    source: str = Field("csv", max_length=200)


class SeasonalityImportFinish(BaseModel):
    source: str = Field("csv", max_length=200)
    last_date: date | None = None


@app.post("/api/seasonality/import")
def seasonality_import(p: SeasonalityImportChunk, db: Session = Depends(get_db), _: User = Depends(admin_user)):
    """Month-end closes extracted in the browser from the user's historical OHLCV CSV."""
    from app.services import seasonality as ss

    return ss.import_chunk(db, p.rows, p.replace_all, p.source)


@app.post("/api/seasonality/import/finish")
def seasonality_import_finish(p: SeasonalityImportFinish, db: Session = Depends(get_db), _: User = Depends(admin_user)):
    from app.services import seasonality as ss

    return ss.finish_import(db, p.source, p.last_date) | {"coverage": ss.coverage(db)}


# ------------------------------------------------------------------ ratings tab
@app.get("/api/ratings/analysts")
def ratings_analysts(min_strong_buy: int = 1, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Stocks with at least ``min_strong_buy`` analysts rating Strong Buy (cached counts)."""
    from app.services import ratings

    return ratings.analyst_rows(db, max(1, min_strong_buy))


@app.post("/api/ratings/analysts/load")
async def ratings_analysts_load(limit: int = 100, db: Session = Depends(get_db), _: User = Depends(current_user)):
    from app.services import ratings

    return await ratings.load_analysts(db, make_datasource(), min(max(limit, 1), 200))


@app.get("/api/ratings/sentisense")
def ratings_sentisense(db: Session = Depends(get_db), _: User = Depends(current_user)):
    from app.services import ratings

    return ratings.sentisense_rows(db)


@app.post("/api/ratings/sentisense/load")
async def ratings_sentisense_load(db: Session = Depends(get_db), _: User = Depends(current_user)):
    from app.services import ratings

    return await ratings.load_sentisense(db, make_datasource())


# ------------------------------------------------------------------ custom watchlist
class WatchAdd(BaseModel):
    symbols: str = Field(..., min_length=1, max_length=2000)  # "AAPL, MSFT nvda"
    note: str | None = Field(None, max_length=500)


@app.get("/api/custom-watchlist")
async def custom_watchlist(live: bool = True, db: Session = Depends(get_db), u: User = Depends(current_user)):
    from app.models import CustomWatchItem
    from app.services import research as rs

    items = db.scalars(select(CustomWatchItem).where(CustomWatchItem.user_id == u.id)
                       .order_by(CustomWatchItem.added_at.desc(), CustomWatchItem.id.desc())).all()
    syms = [i.symbol for i in items]
    snaps: dict[str, dict] = {}  # no stored daily history (the Bounces loader was removed): prices are live
    batch, opts = rs.options_rows(db, syms)
    insider = rs.insider_summary(db, syms)
    an = rs.analyst_counts(db, syms)
    sh = rs.shariah_map(db, set(syms))
    earn = rs.earnings_markers(db, 30)
    quotes: dict[str, dict] = {}
    api_bars: dict[str, tuple[list[dict], str]] = {}
    if live and syms:
        ds = make_datasource()
        end = rs.today_ny()

        async def one(s):
            try:
                q, prov = await asyncio.wait_for(ds.company_quote(s), 10)
                quotes[s] = q | {"source": prov.provider}
            except Exception:  # noqa: BLE001 - a failing quote never breaks the list
                pass
            bars, src = [], None
            try:
                df, prov = await asyncio.wait_for(ds.bars(s, end - timedelta(days=30), end), 10)
                bars = [{"date": ts.date().isoformat(), "open": float(r.open), "high": float(r.high), "low": float(r.low),
                         "close": float(r.close), "volume": float(r.volume)} for ts, r in df.tail(15).iterrows()]
                src = prov.provider
            except Exception:  # noqa: BLE001
                pass
            try:  # today's bar from the live price feed (Alpaca / Finnhub) when history ends earlier
                q, qp = await asyncio.wait_for(ds.quote(s), 10)
                ts = q.get("market_ts")
                day = ts.astimezone(mc.NY).date().isoformat() if ts else None
                if q.get("price") and q.get("open") and day and (not bars or day > bars[-1]["date"]):
                    bars.append({"date": day, "open": q["open"], "high": q.get("high"), "low": q.get("low"),
                                 "close": q["price"], "volume": q.get("volume")})
                    src = f"{src} + {qp.provider} (today)" if src else qp.provider
            except Exception:  # noqa: BLE001
                pass
            if bars:
                api_bars[s] = (bars[-15:], src)

        await asyncio.gather(*(one(s) for s in syms[:60]))
    rows = []
    for i in items:
        s, o, a, q = i.symbol, opts.get(i.symbol), an.get(i.symbol), quotes.get(i.symbol) or {}
        snap = snaps.get(s) or {}
        bars, bars_src = api_bars.get(s) or (snap.get("bars") or [], "daily market data" if snap.get("bars") else None)
        price = q.get("price") or snap.get("price") or (o.price if o else None)
        rows.append({"symbol": s, "note": i.note, "added_at": i.added_at.isoformat(), "name": q.get("name"),
                     "price": price, "price_source": q.get("source") if q.get("price") else
                     (f"daily close {snap['date']}" if snap.get("price") else ("options CSV" if o and o.price else None)),
                     "day_change_pct": q.get("day_change_pct") if q.get("price") else snap.get("day_change_pct"),
                     "pe": q.get("pe"), "market_cap": q.get("market_cap"),
                     "vs_ema50_pct": snap.get("vs_ema50_pct"), "above_low52_pct": snap.get("above_low52_pct"),
                     "below_high52_pct": snap.get("below_high52_pct"),
                     "options": None if not o else {"vol_change_1m_pct": o.vol_change_1m_pct, "iv_rank": o.iv_rank,
                                                    "put_call": o.pc_ratio},
                     "insider": insider.get(s), "earnings": earn.get(s),
                     "analysts": None if not a else {"strong_buy": a.strong_buy, "buy": a.buy},
                     "shariah_status": sh.get(s), "bars": bars, "bars_source": bars_src})
    return {"rows": rows, "live": live}


@app.post("/api/custom-watchlist")
def custom_watchlist_add(p: WatchAdd, db: Session = Depends(get_db), u: User = Depends(current_user)):
    import re

    from app.models import CustomWatchItem

    syms = [t for t in dict.fromkeys(re.split(r"[\s,;]+", p.symbols.upper())) if t]
    bad = [t for t in syms if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", t)]
    if bad:
        raise HTTPException(422, f"Not a valid ticker: {', '.join(bad[:5])}")
    have = set(db.scalars(select(CustomWatchItem.symbol).where(CustomWatchItem.user_id == u.id)).all())
    added = [s for s in syms if s not in have]
    for s in added:
        db.add(CustomWatchItem(user_id=u.id, symbol=s, note=p.note or None))
    db.commit()
    return {"added": added, "already": [s for s in syms if s in have]}


@app.delete("/api/custom-watchlist/{symbol}")
def custom_watchlist_remove(symbol: str, db: Session = Depends(get_db), u: User = Depends(current_user)):
    from app.models import CustomWatchItem

    db.execute(delete(CustomWatchItem).where(CustomWatchItem.user_id == u.id,
                                             CustomWatchItem.symbol == symbol.upper()))
    db.commit()
    return {"ok": True}


class SeasonalityLoad(BaseModel):
    max_seconds: float = Field(200, ge=20, le=270)
    rebuild: bool = False


@app.post("/api/seasonality/load")
async def seasonality_load(p: SeasonalityLoad, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Load ~10 years of month-end closes for every US stock from the price API (resumable)."""
    from app.services import seasonality as ss

    return await ss.load_api(db, make_datasource(), p.max_seconds, p.rebuild)


async def _attach_prices(db: Session, rows: list[dict]) -> None:
    """Current market price for each row: Alpaca live snapshot, else the latest daily close on file,
    else the last month-end close."""
    from app.models import PriceSnapshot

    syms = [r["symbol"] for r in rows]
    live: dict[str, dict] = {}
    alp = getattr(make_datasource(), "alpaca", None)
    if syms and alp is not None and hasattr(alp, "multi_snapshots"):
        try:
            live = await asyncio.wait_for(alp.multi_snapshots(syms), timeout=15)
        except Exception as e:  # noqa: BLE001
            log.warning("seasonality live prices unavailable: %s", e)
    daily = {s: (d, c) for s, d, c in db.execute(select(PriceSnapshot.symbol, PriceSnapshot.as_of, PriceSnapshot.close)
                                                 .where(PriceSnapshot.symbol.in_(syms))).all()} if syms else {}
    for r in rows:
        snap = live.get(r["symbol"]) or {}
        if snap.get("price"):
            ts = snap.get("market_ts")
            r["price"], r["price_as_of"], r["price_live"] = snap["price"], ts.isoformat() if ts else None, True
        elif r["symbol"] in daily:
            d, c = daily[r["symbol"]]
            r["price"], r["price_as_of"], r["price_live"] = c, d.isoformat(), False
        else:
            r["price"], r["price_as_of"], r["price_live"] = r.get("last_close"), None, False


@app.get("/api/seasonality")
async def seasonality(month: int | None = None, min_years: int = 5, rank_by: str = "pct", limit: int = 500,
                      min_price: float | None = None, max_price: float | None = None, q: str | None = None,
                      mode: str = "bullish", index: str = "all",
                      db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Stocks ranked by how often (and how much) they rose — or fell — in a calendar month."""
    from app.services import indexes as ix
    from app.services import research as rs
    from app.services import seasonality as ss

    month = rs.today_ny().month if month is None else month
    if not 1 <= month <= 12:
        raise HTTPException(422, "month must be 1-12")
    min_years, limit = max(1, min_years), min(max(limit, 10), 2000)
    from app.models import SeasonalityStat

    if db.scalar(select(SeasonalityStat.symbol).where(SeasonalityStat.pos_years.is_(None)).limit(1)):
        ss.recompute(db)  # statistics computed before the bullish/bearish columns existed
    symbols, names, idx_error = None, None, None
    if index in ix.INDEXES:
        from app.models import IndexMember

        symbols = await ix.members(db, make_datasource(), index)
        names = dict(db.execute(select(IndexMember.symbol, IndexMember.name).where(IndexMember.index_code == index)).all())
        if not symbols:
            idx_error = f"Could not load the {ix.INDEXES[index]['label']} constituents right now."
    out = ss.ranking(db, month, min_years, "avg" if rank_by == "avg" else "pct", limit, min_price, max_price, q,
                     "bearish" if mode == "bearish" else "bullish", symbols, names)
    out["ranked"] = rs.hide_non_compliant(db, out["ranked"])  # scan results hide non-compliant stocks
    for i, e in enumerate(out["ranked"], 1):
        e["rank"] = i
    await _attach_prices(db, out["ranked"])
    return out | {"coverage": ss.coverage(db), "load": ss.api_status(db), "index": index,
                  "indexes": [{"code": "all", "label": "All US stocks"}] +
                             [{"code": k, "label": v["label"]} for k, v in ix.INDEXES.items()],
                  "index_error": idx_error}


# ------------------------------------------------------------------ internal Shariah screening engine
class ShariahLoad(BaseModel):
    max_seconds: float = Field(200, ge=20, le=270)
    refresh: bool = False


@app.post("/api/shariah/load")
async def shariah_load(p: ShariahLoad, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Download SEC financial figures for every stock (resumable) and classify them."""
    from app.services import shariah as shs

    return await shs.load(db, make_datasource(), p.max_seconds, p.refresh)


def _methods_info(db: Session) -> list[dict]:
    from app.screening import shariah_engine as se
    from app.services import shariah as shs

    ms = se.methodologies(shs.rules_overrides(db))
    return [{"key": k, "label": m["label"], "source": m["source"], "configured": bool(m["source"] and m["tests"]),
             "business": m["business"], "business_tolerance_pct": m["business_tolerance_pct"], "tests": m["tests"],
             "builtin": bool(se.BUILTIN[k]["source"])} for k, m in ms.items()]


@app.get("/api/shariah")
def shariah_list(methodology: str = "FTSE", status: str | None = None, q: str | None = None, limit: int = 500,
                 offset: int = 0, db: Session = Depends(get_db), _: User = Depends(current_user)):
    from app.models import ShariahResultRow
    from app.screening import shariah_engine as se
    from app.services import shariah as shs
    from app.services.engine import load_config

    m = methodology.upper()
    if m not in se.METHOD_KEYS:
        raise HTTPException(422, f"methodology must be one of {', '.join(se.METHOD_KEYS)}")
    col = getattr(ShariahResultRow, m.lower())
    base = select(ShariahResultRow)
    if q:
        base = base.where(ShariahResultRow.symbol.like(q.upper().strip() + "%"))
    counts = {k: 0 for k in (se.HALAL, se.NON_COMPLIANT, se.DOUBTFUL)}
    sub = base.subquery()
    for st, n in db.execute(select(sub.c[m.lower()], func.count()).group_by(sub.c[m.lower()])).all():
        counts[st] = n
    if status in counts:
        base = base.where(col == status)
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = db.scalars(base.order_by(ShariahResultRow.symbol).offset(max(offset, 0)).limit(min(max(limit, 10), 2000))).all()
    out = []
    for r in rows:
        x = r.data["methods"][m]
        out.append({"symbol": r.symbol, "name": r.data.get("name"), "sic": r.data.get("sic"),
                    "sic_description": r.data.get("sic_description"), "period_end": r.data.get("period_end"),
                    "status": x["status"], "status_label": se.LABELS[x["status"]], "business": x["business"],
                    "ratios": x["ratios"], "reasons": x["reasons"]})
    return {"methodology": m, "rows": out, "total": total, "counts": counts, "labels": se.LABELS,
            "methodologies": _methods_info(db), "load": shs.status(db), "primary": load_config(db).shariah.methodology,
            "disclaimer": se.DISCLAIMER}


@app.get("/api/shariah/rules")
def shariah_rules(db: Session = Depends(get_db), _: User = Depends(current_user)):
    from app.screening import shariah_engine as se

    return {"methodologies": _methods_info(db), "numerators": se.NUMERATORS, "denominators": se.DENOMINATORS,
            "ops": se.OPS, "categories": {k: v["label"] for k, v in se.CATEGORIES.items()}}


class ShariahRuleTest(BaseModel):
    key: str = Field(min_length=1, max_length=32)
    label: str = Field(min_length=1, max_length=200)
    numerator: str
    denominator: str
    op: str
    limit: float


class ShariahRulesIn(BaseModel):
    source: str = Field("", max_length=500)
    business: list[str]
    business_tolerance_pct: float | None = None
    tests: list[ShariahRuleTest]


@app.put("/api/shariah/rules/{methodology}")
def shariah_rules_save(methodology: str, p: ShariahRulesIn, db: Session = Depends(get_db), u: User = Depends(admin_user)):
    """Enter a methodology's rules from its official document; results are recomputed."""
    from app.models import ShariahRules
    from app.screening import shariah_engine as se
    from app.services import shariah as shs

    m = methodology.upper()
    if m not in se.METHOD_KEYS:
        raise HTTPException(404, "unknown methodology")
    rules = p.model_dump()
    errs = se.validate_rules(rules)
    if errs:
        raise HTTPException(422, "; ".join(errs))
    db.add(ShariahRules(methodology=m, rules=rules, updated_by=u.email))
    db.commit()
    if shs.status(db)["frames_done"]:
        shs.compute_all(db)
    return {"ok": True, "methodologies": _methods_info(db)}


class ShariahPrimary(BaseModel):
    methodology: str


@app.put("/api/shariah/primary")
def shariah_primary(p: ShariahPrimary, db: Session = Depends(get_db), u: User = Depends(admin_user)):
    """Methodology used for the Shariah colour on tickers across the app (saved as a settings change)."""
    from app.screening import shariah_engine as se

    m = p.methodology.upper()
    if m not in se.METHOD_KEYS:
        raise HTTPException(422, f"methodology must be one of {', '.join(se.METHOD_KEYS)}")
    cfg = load_config(db).model_dump(by_alias=True)
    cfg["shariah"]["methodology"] = m
    cfg = ScreeningConfig.model_validate(cfg)
    db.add(AppSettings(config=cfg.model_dump(by_alias=True), updated_by=u.email, note=f"Shariah colours use {m}"))
    db.commit()
    return {"primary": m}


@app.get("/api/shariah/{ticker}")
def shariah_detail(ticker: str, db: Session = Depends(get_db), _: User = Depends(current_user)):
    from app.services import shariah as shs

    d = shs.detail(db, ticker)
    if d is None:
        raise HTTPException(404, "unknown ticker")
    return d | {"methodologies": _methods_info(db)}


@app.get("/api/methodology")
def methodology():
    from app.screening import analyst, float_liquidity, insider, options, ranking, shariah, technicals

    return {k: (m.__doc__ or "").strip() for k, m in {"options": options, "insider": insider, "analyst": analyst,
                                                       "technical": technicals, "float_liquidity": float_liquidity,
                                                       "shariah": shariah, "ranking": ranking}.items()}
