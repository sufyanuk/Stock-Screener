import asyncio
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.backtest.engine import BacktestParams, PointInTimeDataSource, run_backtest, simulate_trade
from app.config import ScreeningConfig
from app.models import Recommendation, ScanResult
from app.services.engine import ScanEngine
from tests.conftest import AS_OF, FakeDataSource, oscillating_bars


def orats(cfg: ScreeningConfig | None = None) -> ScreeningConfig:
    """These tests exercise the ORATS/API options path; CSV imports are covered in test_options_import."""
    cfg = cfg or ScreeningConfig()
    cfg.options.source = "orats"
    return cfg


def run_scan(db, ds=None, cfg=None, kind="close"):
    eng = ScanEngine(db, ds or FakeDataSource(), orats(cfg))
    return asyncio.run(eng.run(kind=kind, as_of=AS_OF, live=True))


def test_end_to_end_scan_selects_only_fully_qualified(db):
    run = run_scan(db)
    assert run.status == "ok", run.errors
    assert run.funnel["price_filtered_out"] == 1  # PRICY ($150)
    recs = db.scalars(select(Recommendation).where(Recommendation.run_id == run.id)).all()
    assert [r.ticker for r in recs] == ["GOOD"]
    rec = recs[0]
    assert rec.is_final and rec.stop < rec.entry and rec.target1 > rec.entry
    res = {r.ticker: r for r in db.scalars(select(ScanResult).where(ScanResult.run_id == run.id)).all()}
    assert res["BANK"].shariah_status == "Non-Compliant" and not res["BANK"].eligible
    assert "PCR" not in res  # failed hard options filter at stage 1 (P/C = 0.33)
    good = res["GOOD"].criteria
    assert good["insider"]["metrics"]["aggregate_pct"] == pytest.approx(2.0)  # joint report counted once
    assert good["options"]["metrics"]["volume_change_pct"] == pytest.approx(830, rel=0.01)
    assert good["float"]["metrics"]["category"] == "Low Float"


def test_provider_outage_is_recorded_and_never_passes(db):
    cfg = ScreeningConfig.model_validate({"analyst": {"hard": True}})
    run = run_scan(db, FakeDataSource(fail_analyst=True), cfg)
    assert run.status == "partial" and any("simulated outage" in e for e in run.errors)
    assert db.scalars(select(Recommendation).where(Recommendation.run_id == run.id)).all() == []
    res = db.scalars(select(ScanResult).where(ScanResult.ticker == "GOOD")).first()
    assert res.criteria["analyst"]["outcome"] == "missing"


def test_relaxed_config_is_recorded(db):
    cfg = ScreeningConfig.model_validate({"options": {"hard": False}})
    run = run_scan(db, cfg=cfg)
    assert "options" in run.relaxed_criteria
    assert run.funnel["stage2"] == 3  # PCR now reaches stage 2 (preference, not filter)


def test_simulate_trade_conservative_same_bar():
    df = oscillating_bars()
    d = df.index[-10].date()
    p = BacktestParams(start=d, end=df.index[-1].date(), slippage_bps=0, commission_per_trade=0)
    sim = simulate_trade(df, d, stop=df["low"].iloc[-9] + 0.0001, target=df["high"].iloc[-9] - 0.0001, p=p)
    assert sim[3] == "stop"


def test_backtest_runs_point_in_time(db):
    run_scan(db)  # ingest data
    eng = ScanEngine(db, PointInTimeDataSource(FakeDataSource()), orats())
    out = asyncio.run(run_backtest(eng, BacktestParams(start=date(2026, 10, 26), end=AS_OF)))
    assert out["sessions"] == 5
    # options spike only exists on AS_OF; earlier sessions produce no signals
    assert out["signals"] <= 1
    assert out["limitations"]


@pytest.fixture
def client(db, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "make_datasource", lambda: FakeDataSource())
    return TestClient(main.app)


def _auth(client, email="admin@example.com"):
    r = client.post("/api/auth/register", json={"email": email, "password": "correct horse battery"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}, r.json()["role"]


def test_api_auth_and_roles(client):
    assert client.get("/api/dashboard").status_code == 401
    admin, role = _auth(client)
    assert role == "admin"
    user, role2 = _auth(client, "user@example.com")
    assert role2 == "user"
    assert client.post("/api/auth/login", json={"email": "user@example.com", "password": "wrong password!"}).status_code == 401
    assert client.put("/api/admin/settings", headers=user, json={"config": {}, "note": "try"}).status_code == 403
    r = client.put("/api/admin/settings", headers=admin, json={"config": {"max_price": 200}, "note": "bad"})
    assert r.status_code == 422
    r = client.put("/api/admin/settings", headers=admin, json={"config": {"options": {"max_put_call_ratio": 0.3}}, "note": "loosen"})
    assert r.status_code == 200 and "options.max_put_call_ratio" in r.json()["relaxed"]
    assert client.get("/api/admin/settings/history", headers=admin).json()[0]["note"] == "loosen"


def test_api_dashboard_recommendations_revisions(client, db):
    admin, _ = _auth(client)
    run_scan(db)
    d = client.get("/api/dashboard", headers=admin).json()
    assert [t["ticker"] for t in d["top"]] == ["GOOD"]
    assert d["insider_buys"] == [] or d["insider_buys"][0]["ticker"]
    rec = d["top"][0]
    r = client.post(f"/api/recommendations/{rec['id']}/revise", headers=admin,
                    json={"reason": "corrected stop after data fix", "changes": {"stop": 19.0}})
    assert r.status_code == 200 and r.json()["stop"] == 19.0 and r.json()["revisions"][0]["before"]["stop"] == rec["stop"]
    orig = db.get(Recommendation, rec["id"])
    assert orig.stop == rec["stop"]  # original row untouched
    assert client.post(f"/api/recommendations/{rec['id']}/revise", headers=admin,
                       json={"reason": "bad field", "changes": {"score": 99}}).status_code == 422
    s = client.get("/api/stocks/GOOD", headers=admin).json()
    assert s["result"]["ticker"] == "GOOD" and len(s["bars"]) > 100
    ext = client.get("/api/stocks/GOOD/external", headers=admin).json()
    assert ext["news"] == [] and "filings_error" in ext  # fake source has no SEC client -> explicit error
    h = client.get("/api/recommendations/history", headers=admin).json()
    assert h[0]["ticker"] == "GOOD"


def test_cookie_session_and_csrf(client):
    r = client.post("/api/auth/register", json={"email": "c@example.com", "password": "correct horse battery"})
    cookie = r.headers["set-cookie"].lower()
    assert "si_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/api/me").json()["email"] == "c@example.com"  # cookie alone authenticates reads
    body = {"ticker": "X", "side": "buy", "quantity": 1, "price": 1, "trade_date": "2026-10-01"}
    assert client.post("/api/journal/fills", json=body).status_code == 403  # write without CSRF header
    assert client.post("/api/journal/fills", json=body, headers={"X-Requested-With": "stockintel"}).status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/me").status_code == 401


def test_dashboard_dedupes_joint_insider_reports_and_so_date(client, db):
    admin, _ = _auth(client)
    run_scan(db)
    d = client.get("/api/dashboard", headers=admin).json()
    good = [b for b in d["insider_buys"] if b["ticker"] == "GOOD"]
    assert len(good) == 1 and good[0]["owner"] == "Jane Doe" and good[0]["co_reporters"] == ["Doe Trust"]
    ins = d["top"][0]["criteria"]["insider"]["metrics"]
    assert ins["shares_outstanding_date"] == "2026-09-30"  # SEC report date, not the scan date


def test_api_tools_and_journal(client):
    h, _ = _auth(client)
    r = client.post("/api/tools/position", json={"account_balance": 3000, "risk_pct": 2, "entry": 100, "stop": 95}).json()
    assert r["shares"] == 12
    r = client.post("/api/tools/expectancy", json={"win_rate_pct": 50, "avg_win": 200, "avg_loss": 100}).json()
    assert r["expectancy_per_trade"] == 50 and r["scenarios"]
    assert client.post("/api/tools/position", json={"account_balance": 3000, "risk_pct": 2, "entry": 100, "stop": 105}).status_code == 422
    client.post("/api/journal/fills", headers=h, json={"ticker": "good", "side": "buy", "quantity": 10, "price": 20, "trade_date": "2026-10-01"})
    client.post("/api/journal/fills", headers=h, json={"ticker": "GOOD", "side": "sell", "quantity": 10, "price": 22, "fees": 1, "trade_date": "2026-10-10"})
    s = client.get("/api/journal/summary", headers=h).json()
    assert s["realized_pnl"] == 19 and s["win_rate_pct"] == 100
    other, _ = _auth(client, "other@example.com")
    assert client.get("/api/journal/fills", headers=other).json() == []  # per-user isolation
