from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.providers.market import parse_av_earnings
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
    before = client.get("/api/insiders?days=30", headers=h).json()
    assert before["rows"] == [] and before["shares_missing"] == 3 and before["total_purchases"] == 3
    ld = client.post("/api/insiders/load-shares", headers=h).json()
    assert ld["fetched"] == 2 and ld["remaining"] == 0
    r = client.get("/api/insiders?days=30", headers=h).json()
    good = [x for x in r["rows"] if x["ticker"] == "GOOD"]
    assert len(good) == 1 and good[0]["co_reporters"] == ["Doe Trust"] and good[0]["shariah_status"] == "Compliant"
    assert good[0]["outstanding_shares"] == 100_000_000 and good[0]["pct_outstanding"] == pytest.approx(2.0)
    assert [x["ticker"] for x in r["rows"]] == ["GOOD"]  # PCR 0.5% hidden, BANK has no share count
    assert r["shares_unavailable"] == 1 and r["shares_missing"] == 0
    assert len(client.get("/api/insiders?days=30&min_pct=0.1", headers=h).json()["rows"]) == 2
    from tests.test_options_import import fake_csv
    client.post("/api/admin/options-import", headers=h, json={"filename": "u.csv", "csv": fake_csv()})
    bid = client.get("/api/options-imports", headers=h).json()[0]["id"]
    d = client.get(f"/api/options-imports/{bid}", headers=h).json()
    rows = {x["symbol"]: x for x in d["data"]}
    assert rows["GOOD"]["passes_rules"] and not rows["PCR"]["passes_rules"] and not rows["PRICY"]["passes_rules"]
    assert rows["GOOD"]["in_top5"] and rows["BANK"]["shariah_status"] == "Non-Compliant"
    assert d["thresholds"]["min_volume_change_pct"] == 300


def _month_rows(years=range(2015, 2026)):
    """GOOD gains 10% every November, BANK loses 5% every November, FLAT never moves, PENNY is cheap."""
    rows = []
    for sym, f in {"GOOD": 1.10, "BANK": 0.95, "FLAT": 1.0, "PENNY": 1.0}.items():
        px = 0.5 if sym == "PENNY" else 100.0
        for y in years:
            for m in range(1, 13):
                if m == 11:
                    px *= f
                rows.append([sym, f"{y}-{m:02d}", px])
    return rows


def test_month_last_session():
    assert ss.month_last_session(date(2026, 5, 1)) == date(2026, 5, 29)
    assert ss.month_last_session(date(2026, 10, 1)) == date(2026, 10, 30)


def test_seasonality_from_imported_csv(client, db):
    h = _auth(client)
    rows = _month_rows() + [["abc.w!", "2020-01", 1.0], ["BAD", "2020-13", 1.0], ["NEG", "2020-01", -1]]
    r = client.post("/api/seasonality/import", headers=h, json={"rows": rows, "replace_all": True, "source": "prices.csv"}).json()
    assert r["stored"] == 4 * 11 * 12 and r["skipped"] == 3
    f = client.post("/api/seasonality/import/finish", headers=h, json={"source": "prices.csv", "last_date": "2025-12-31"}).json()
    assert f["partial_month_dropped"] is None and f["coverage"]["symbols"] == 4 and f["coverage"]["months"] == 132
    assert f["coverage"]["provider"] == "prices.csv"

    t = client.get("/api/seasonality?month=11&min_years=5", headers=h).json()
    syms = [e["symbol"] for e in t["ranked"]]
    assert syms[0] == "GOOD" and syms[-1] == "BANK"
    good = t["ranked"][0]
    assert good["avg"] == pytest.approx(10.0) and good["win_rate"] == 100 and good["years"] == 11
    assert good["heatmap"][10] == pytest.approx(10.0) and good["heatmap"][0] == pytest.approx(0.0)
    assert t["ranked"][-1]["avg"] == pytest.approx(-5.0)
    t2 = client.get("/api/seasonality?month=11&min_price=1", headers=h).json()
    assert "PENNY" not in [e["symbol"] for e in t2["ranked"]]
    assert [e["symbol"] for e in client.get("/api/seasonality?month=11&q=go", headers=h).json()["ranked"]] == ["GOOD"]
    nov = next(m for m in t["best_by_month"] if m["month"] == 11)
    assert nov["top"][0]["symbol"] == "GOOD"
    assert client.get("/api/seasonality?month=0", headers=h).status_code == 422

    # merge mode: re-importing one symbol replaces only that symbol's history
    client.post("/api/seasonality/import", headers=h, json={"rows": [["GOOD", "2025-01", 10.0], ["GOOD", "2025-02", 11.0]]})
    f = client.post("/api/seasonality/import/finish", headers=h, json={"last_date": "2025-02-10"}).json()
    assert f["partial_month_dropped"] is None  # Feb 2025 is not the latest month (Dec 2025 is)
    feb = client.get("/api/seasonality?month=2&min_years=1&q=GOOD", headers=h).json()["ranked"]
    assert len(feb) == 1 and feb[0]["years"] == 1 and feb[0]["avg"] == pytest.approx(10.0)  # only the new history
    assert client.get("/api/seasonality?month=11&min_years=1&q=GOOD", headers=h).json()["ranked"] == []


def test_partial_final_month_is_dropped(client, db):
    h = _auth(client)
    rows = _month_rows(range(2020, 2023)) + [["GOOD", "2023-01", 500.0]]
    client.post("/api/seasonality/import", headers=h, json={"rows": rows, "replace_all": True})
    f = client.post("/api/seasonality/import/finish", headers=h, json={"last_date": "2023-01-13"}).json()
    assert f["partial_month_dropped"] == "2023-01"
    jan = client.get("/api/seasonality?month=1&min_years=1&q=GOOD", headers=h).json()["ranked"][0]
    assert jan["avg"] == pytest.approx(0.0)  # the half-month +400% move was not counted


def test_seasonality_import_requires_admin(client, db):
    _auth(client)
    r2 = client.post("/api/auth/register", json={"email": "b@example.com", "password": "correct horse battery"})
    h2 = {"Authorization": f"Bearer {r2.json()['token']}"}
    assert client.post("/api/seasonality/import", headers=h2, json={"rows": []}).status_code == 403


def test_alpha_vantage_overview_counts():
    from app.providers.market import parse_av_overview

    o = parse_av_overview({"Symbol": "IBM", "AnalystTargetPrice": "250.5", "AnalystRatingStrongBuy": "3",
                           "AnalystRatingBuy": "7", "AnalystRatingHold": "9", "AnalystRatingSell": "1",
                           "AnalystRatingStrongSell": "None"})
    assert o["counts"] == {"strong_buy": 3, "buy": 7, "hold": 9, "sell": 1} and o["target"] == 250.5
    assert parse_av_overview({"Information": "rate limit"}) is None
