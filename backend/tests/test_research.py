import asyncio
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.providers.market import parse_av_earnings
from app.services import research as rs
from app.services import seasonality as ss
from tests.conftest import FakeDataSource
from tests.test_engine_api import run_scan


@pytest.fixture
def client(db, monkeypatch):
    import app.main as main

    monkeypatch.setattr(main, "make_datasource", lambda: FakeDataSource())
    return TestClient(main.app)


def _auth(client):
    r = client.post("/api/auth/register", json={"email": "a@example.com", "password": "correct horse battery"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_alpha_vantage_calendar_parser_matches_spreadsheet_format():
    rows = parse_av_earnings("symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\n"
                             "AAUC,ALLIED GOLD CORPORATION,2026-11-04,2026-09-30,0.18,USD,post-market\n"
                             "ACET,ADICET BIO,2026-11-04,2026-09-30,-1.56,USD,\n")
    assert rows[0] == {"symbol": "AAUC", "name": "ALLIED GOLD CORPORATION", "report_date": "2026-11-04",
                       "fiscal_date_ending": "2026-09-30", "eps_estimate": 0.18, "time_of_day": "post-market"}
    assert rows[1]["eps_estimate"] == -1.56 and rows[1]["time_of_day"] is None


def test_earnings_screen_columns_and_markers(client, db):
    h = _auth(client)
    run_scan(db)  # stores insider purchases + Shariah statuses for GOOD / BANK
    r = client.get("/api/earnings/upcoming?days=7", headers=h).json()
    syms = [x["symbol"] for x in r["rows"]]
    assert syms == ["GOOD", "BANK"]  # LATE (10 days out) excluded
    good = r["rows"][0]
    assert good["time_of_day"] == "post-market" and good["shariah_status"] == "Compliant"
    assert good["insider"]["purchases"] == 1 and good["insider"]["shares"] == 2_000_000  # joint filing counted once
    assert good["analysts"] is None and r["analysts_missing"] == 2
    loaded = client.post("/api/earnings/load-analysts?days=7", headers=h).json()
    assert loaded["fetched"] == 2 and loaded["remaining"] == 0
    r = client.get("/api/earnings/upcoming?days=7", headers=h).json()
    assert r["rows"][0]["analysts"]["strong_buy"] == 3
    m = client.get("/api/earnings/markers", headers=h).json()["markers"]
    assert set(m) == {"GOOD", "BANK"} and m["GOOD"]["time_of_day"] == "post-market"


def test_stock_panel_summary(client, db):
    h = _auth(client)
    run_scan(db)
    client.get("/api/earnings/markers", headers=h)
    s = client.get("/api/stocks/GOOD/summary", headers=h).json()
    assert s["price"] == 20.3 and s["pe"] == 18.4 and s["float_ratio_pct"] == pytest.approx(15.0)
    assert s["analysts"]["recommending_buy"] == 8 and s["analysts"]["counts"]["strong_buy"] == 3
    assert s["earnings"]["time_of_day"] == "post-market" and len(s["bars"]) > 100 and s["news"][0]["title"]
    assert s["insider_90d"]["purchases"] == 1 and s["shariah_status"] == "Compliant"


def test_dashboard_no_longer_has_low_float_or_health(client, db):
    h = _auth(client)
    run_scan(db)
    d = client.get("/api/dashboard", headers=h).json()
    assert "low_float" not in d and "provider_health" not in d


def test_insiders_endpoint_and_options_rows(client, db):
    h = _auth(client)
    run_scan(db)
    r = client.get("/api/insiders?days=30", headers=h).json()
    good = [x for x in r["rows"] if x["ticker"] == "GOOD"]
    assert len(good) == 1 and good[0]["co_reporters"] == ["Doe Trust"] and good[0]["shariah_status"] == "Compliant"
    assert client.get("/api/insiders?days=13", headers=h).json()["days"] == 30  # invalid -> default
    from tests.test_options_import import fake_csv
    client.post("/api/admin/options-import", headers=h, json={"filename": "u.csv", "csv": fake_csv()})
    bid = client.get("/api/options-imports", headers=h).json()[0]["id"]
    d = client.get(f"/api/options-imports/{bid}", headers=h).json()
    rows = {x["symbol"]: x for x in d["data"]}
    assert rows["GOOD"]["passes_rules"] and not rows["PCR"]["passes_rules"] and not rows["PRICY"]["passes_rules"]
    assert rows["GOOD"]["in_top5"] and rows["BANK"]["shariah_status"] == "Non-Compliant"
    assert d["thresholds"]["min_volume_change_pct"] == 300


def test_month_end_sessions_exclude_current_month():
    t = ss.month_end_sessions(1, date(2026, 11, 5))
    assert t[-1] == (date(2026, 10, 1), date(2026, 10, 30)) and len(t) == 13
    assert (date(2026, 5, 1), date(2026, 5, 29)) in t  # last session of May 2026 (Memorial Day is 25th)


def test_whole_market_seasonality(client, db):
    h = _auth(client)
    FakeDataSource.market_calls = 0
    r = client.post("/api/seasonality/refresh", headers=h, json={"years": 10, "max_months": 36}).json()
    assert r["fetched"] == 36 and r["remaining"] == 121 - 36 and FakeDataSource.market_calls == 36
    while r["remaining"]:
        r = client.post("/api/seasonality/refresh", headers=h, json={"years": 10, "max_months": 36}).json()
    assert FakeDataSource.market_calls == 121  # one request per month for the whole market
    again = client.post("/api/seasonality/refresh", headers=h, json={"years": 10}).json()
    assert again["fetched"] == 0 and FakeDataSource.market_calls == 121
    assert r["coverage"]["symbols"] == 4  # warrant and 6+ letter symbols filtered out

    t = client.get("/api/seasonality?month=11&min_years=5", headers=h).json()
    syms = [e["symbol"] for e in t["ranked"]]
    assert syms[0] == "GOOD" and syms[-1] == "BANK"
    good = t["ranked"][0]
    assert good["avg"] == pytest.approx(10.0) and good["win_rate"] == 100 and good["years"] == 10
    assert good["heatmap"][10] == pytest.approx(10.0) and good["heatmap"][0] == pytest.approx(0.0)
    assert t["ranked"][-1]["avg"] == pytest.approx(-5.0)
    # price filter removes the penny stock; search filter
    t2 = client.get("/api/seasonality?month=11&min_price=1", headers=h).json()
    assert "PENNY" not in [e["symbol"] for e in t2["ranked"]]
    assert [e["symbol"] for e in client.get("/api/seasonality?month=11&q=go", headers=h).json()["ranked"]] == ["GOOD"]
    nov = next(m for m in t["best_by_month"] if m["month"] == 11)
    assert nov["top"][0]["symbol"] == "GOOD"
    assert client.get("/api/seasonality?month=0", headers=h).status_code == 422


def test_old_single_stock_rows_do_not_count_as_loaded(client, db):
    from app.models import MonthlyClose

    h = _auth(client)
    db.add(MonthlyClose(symbol="OLD", month=date(2026, 9, 1), close=10.0, provider="massive"))  # pre-existing row
    db.commit()
    FakeDataSource.market_calls = 0
    r = client.post("/api/seasonality/refresh", headers=h, json={"years": 3, "max_months": 36}).json()
    assert r["fetched"] == 36 and FakeDataSource.market_calls == 36  # Sep 2026 fetched despite the old row
    assert r["coverage"]["symbols"] >= 4
