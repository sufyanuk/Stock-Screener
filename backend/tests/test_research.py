import asyncio
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.providers.market import parse_av_earnings
from app.services import research as rs
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


def test_seasonality_math_excludes_gaps_and_current_month():
    today = rs.today_ny()
    closes = [(date(2020, 1, 1), 100), (date(2020, 2, 1), 110), (date(2020, 4, 1), 121),  # Mar missing -> no Apr return
              (date(2021, 1, 1), 100), (date(2021, 2, 1), 90),
              (date(today.year, today.month, 1), 50)]  # unfinished month ignored
    rets = rs.monthly_returns(closes)
    assert (2020, 2, pytest.approx(10.0)) in [(y, m, pytest.approx(r)) for y, m, r in rets]
    assert all(m != 4 for _, m, _ in rets)
    st = rs.seasonality_stats(closes)
    assert st[2]["years"] == 2 and st[2]["avg"] == pytest.approx(0.0) and st[2]["win_rate"] == 50
    assert st[2]["best_year"] == 2020 and st[2]["worst_year"] == 2021


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


def test_seasonality_ranking(client, db):
    h = _auth(client)
    custom = {"universe": "custom", "symbols": "GOOD, BANK"}
    r = client.post("/api/seasonality/refresh", headers=h, json=custom | {"years": 10}).json()
    assert r["fetched"] == 2 and r["remaining"] == 0
    again = client.post("/api/seasonality/refresh", headers=h, json=custom).json()
    assert again["fetched"] == 0  # cached
    t = client.get("/api/seasonality?month=11&universe=custom&symbols=GOOD,BANK&min_years=5", headers=h).json()
    assert [e["symbol"] for e in t["ranked"]] == ["GOOD", "BANK"]
    assert t["ranked"][0]["avg"] == pytest.approx(10.0) and t["ranked"][0]["win_rate"] == 100
    assert t["ranked"][1]["avg"] == pytest.approx(-5.0) and t["ranked"][0]["heatmap"][10] == pytest.approx(10.0)
    nov = next(m for m in t["best_by_month"] if m["month"] == 11)
    assert nov["top"][0]["symbol"] == "GOOD"
    assert client.get("/api/seasonality?month=13", headers=h).status_code == 422


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
