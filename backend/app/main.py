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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import market_calendar as mc
from app.auth import SESSION_COOKIE, admin_user, create_token, current_user, hash_password, verify_password
from app.backtest.engine import BacktestParams, PointInTimeDataSource, run_backtest
from app.config import ScreeningConfig, get_settings
from app.db import SessionLocal, get_db
from app.models import (AppSettings, BusinessReviewRow, DailyBar, InsiderTransaction, JournalDividend, JournalFill,
                        OptionsImportBatch, OptionsImportRow, ProviderHealth, Recommendation, RecommendationRevision,
                        ScanResult, ScanRun, Security, User)
from app.screening.shariah import METHODOLOGIES, purification
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
              "delayed or incomplete. Shariah screening is an analytical aid, not a fatwa.")


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


# ------------------------------------------------------------------ dashboard
@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db), _: User = Depends(current_user)):
    run = _latest_run(db)
    now = datetime.now(timezone.utc)
    out: dict = {"disclaimer": DISCLAIMER, "market_phase": mc.market_phase(now), "now": now.isoformat(),
                 "latest_run": _run_dict(run), "top": [], "watchlist": [], "unusual_options": [], "near_support": [],
                 "low_float": [], "shariah_counts": {}, "insider_buys": []}
    if run:
        recs = db.scalars(select(Recommendation).where(Recommendation.run_id == run.id).order_by(Recommendation.rank)).all()
        out["top"] = [_effective(db, r) for r in recs]
        results = db.scalars(select(ScanResult).where(ScanResult.run_id == run.id)).all()
        out["watchlist"] = [_result_dict(r) for r in results if (r.extra or {}).get("watchlist")]
        opt = [r for r in results if r.criteria.get("options", {}).get("outcome") == "pass"]
        out["unusual_options"] = [{"ticker": r.ticker, "company": r.company, **{k: r.criteria["options"]["metrics"].get(k) for k in (
            "current_volume", "baseline_volume", "volume_change_pct", "iv_rank", "put_call_ratio", "call_volume", "put_volume",
            "current_session")}} for r in sorted(opt, key=lambda r: -(r.criteria["options"]["metrics"].get("volume_change_pct") or 0))[:20]]
        out["near_support"] = [{"ticker": r.ticker, "price": r.price, "classification": r.criteria.get("technical", {}).get("metrics", {}).get("classification"),
                                "distance_pct": r.criteria.get("technical", {}).get("metrics", {}).get("support_distance_pct")}
                               for r in results if r.criteria.get("technical", {}).get("metrics", {}).get("classification")
                               in ("strong_support", "near_support", "approaching_support")][:20]
        out["low_float"] = [{"ticker": r.ticker, "float_ratio_pct": r.criteria["float"]["metrics"].get("float_ratio_pct"),
                             "category": r.criteria["float"]["metrics"].get("category")}
                            for r in results if r.criteria.get("float", {}).get("metrics", {}).get("category") in ("Very Low Float", "Low Float")][:20]
        counts: dict = {}
        for r in results:
            counts[r.shariah_status] = counts.get(r.shariah_status, 0) + 1
        out["shariah_counts"] = counts
    since = date.today() - timedelta(days=7)
    buys = db.scalars(select(InsiderTransaction).where(InsiderTransaction.code == "P", InsiderTransaction.acquired_disposed == "A",
                                                       InsiderTransaction.is_derivative.is_(False),
                                                       InsiderTransaction.filing_date >= since)
                      .order_by(InsiderTransaction.filing_date.desc(), InsiderTransaction.direct_indirect)
                      .limit(200)).all()
    # Same rule as the insider criterion: joint/indirect reports of one purchase are shown once.
    seen: dict[tuple, dict] = {}
    for b in buys:
        key = (b.issuer_cik, b.transaction_date, round(b.shares, 4), round(b.price or 0, 4))
        if key in seen:
            if b.owner_name not in seen[key]["co_reporters"] and b.owner_name != seen[key]["owner"]:
                seen[key]["co_reporters"].append(b.owner_name)
            continue
        seen[key] = {"ticker": b.ticker, "owner": b.owner_name, "co_reporters": [],
                     "title": b.officer_title or ("Director" if b.is_director else ""),
                     "transaction_date": b.transaction_date.isoformat(), "filing_date": b.filing_date.isoformat(),
                     "shares": b.shares, "price": b.price, "value": (b.price or 0) * b.shares}
    out["insider_buys"] = list(seen.values())[:25]
    out["provider_health"] = health_rows(db)
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
    return {"run": _run_dict(run), "top": [_effective(db, r) for r in recs], "message": msg}


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
                    "hit_stop": hit_stop, "hit_target1": hit_t1})
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
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_RUN_MINUTES)
    return db.scalars(select(ScanRun.id).where(ScanRun.status == "running", ScanRun.started_at >= cutoff)
                      .limit(1)).first() is not None


async def _do_scan(kind: str, by: str) -> dict:
    async with _scan_lock:
        with SessionLocal() as db:
            ds = make_datasource()
            eng = ScanEngine(db, ds)
            eng.errors.extend(getattr(ds, "errors", []))
            run = await eng.run(kind=kind, triggered_by=by)
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
            "methodologies": METHODOLOGIES, "defaults": ScreeningConfig().model_dump(by_alias=True)}


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
    return _batch_dict(b) | {"data": [{c: getattr(r, c) for c in (
        "symbol", "name", "price", "change", "pct_change", "iv_rank", "options_volume", "vol_change_1m_pct",
        "put_volume", "call_volume", "pc_ratio")} for r in rows]}


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


# ------------------------------------------------------------------ backtest
@app.post("/api/backtest")
async def backtest(p: BacktestParams, db: Session = Depends(get_db), _: User = Depends(current_user)):
    if (p.end - p.start).days > 400:
        raise HTTPException(422, "maximum backtest window is 400 days per request")
    eng = ScanEngine(db, PointInTimeDataSource(make_datasource()))
    return await run_backtest(eng, p)


@app.get("/api/methodology")
def methodology():
    from app.screening import analyst, float_liquidity, insider, options, ranking, shariah, technicals

    return {k: (m.__doc__ or "").strip() for k, m in {"options": options, "insider": insider, "analyst": analyst,
                                                       "technical": technicals, "float_liquidity": float_liquidity,
                                                       "shariah": shariah, "ranking": ranking}.items()}
