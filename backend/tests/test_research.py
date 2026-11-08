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
    assert t["mode"] == "bullish" and t["ranked"][0]["symbol"] == "GOOD"
    good = t["ranked"][0]
    assert good["avg"] == pytest.approx(10.0) and good["win_rate"] == 100 and good["years"] == 11
    assert (good["count"], good["pct"]) == (11, 100.0)
    assert good["max"] == pytest.approx(10.0) and good["min"] == pytest.approx(10.0) and good["avg_dir"] == pytest.approx(10.0)
    bear = client.get("/api/seasonality?month=11&min_years=5&mode=bearish", headers=h).json()
    b = bear["ranked"][0]
    assert bear["mode"] == "bearish" and b["symbol"] == "BANK" and (b["count"], b["pct"]) == (11, 100.0)
    assert b["max"] == pytest.approx(-5.0) and b["avg_dir"] == pytest.approx(-5.0)
    assert [x["symbol"] for x in t["ranked"]][-1] in ("BANK", "FLAT", "PENNY")
    t2 = client.get("/api/seasonality?month=11&min_price=1", headers=h).json()
    assert "PENNY" not in [e["symbol"] for e in t2["ranked"]]
    assert [e["symbol"] for e in client.get("/api/seasonality?month=11&q=go", headers=h).json()["ranked"]] == ["GOOD"]
    assert "best_by_month" not in t  # Top-5-per-month card removed
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



def test_seasonality_index_filter(client, db, monkeypatch):
    from app.services import indexes as ix

    async def fake_fetch(ds, code):
        return [("GOOD", "Good Widgets Inc"), ("FLAT", "Flat Co")], "wikipedia"

    monkeypatch.setattr(ix, "_fetch", fake_fetch)
    h = _auth(client)
    client.post("/api/seasonality/import", headers=h, json={"rows": _month_rows(), "replace_all": True})
    client.post("/api/seasonality/import/finish", headers=h, json={"last_date": "2025-12-31"})
    r = client.get("/api/seasonality?month=11&index=sp500", headers=h).json()
    assert [x["symbol"] for x in r["ranked"]] == ["GOOD", "FLAT"] and r["ranked"][0]["name"] == "Good Widgets Inc"
    assert {i["code"] for i in r["indexes"]} == {"all", "sp500", "ndx", "dji"}


def test_wikipedia_constituent_parser():
    from app.services.indexes import parse_wiki

    html = ("<table><tr><th>Symbol</th><th>Security</th></tr>"
            + "".join(f"<tr><td>T{i}</td><td>Co {i}</td></tr>" for i in range(30))
            + "<tr><td>BRK.B</td><td>Berkshire</td></tr></table>")
    rows = parse_wiki(html, 25)
    assert len(rows) == 31 and rows[-1] == ("BRK.B", "Berkshire")
    assert parse_wiki(html, 100) == []


def test_seasonality_loads_from_alpaca_monthly_bars(client, db, monkeypatch):
    from datetime import date as d

    import app.main as main
    from app.models import Security
    from app.services import seasonality as ss
    from tests.conftest import FakeDataSource

    calls = []

    class Alp:
        async def multi_daily_bars(self, symbols, start, end, timeframe="1Day"):
            calls.append((tuple(symbols), timeframe))
            out = {}
            for s in symbols:
                px, rows = 100.0, []
                y, m = start.year, start.month
                while d(y, m, 1) <= end:
                    if m == 3 and s == "UPMAR":
                        px *= 1.08
                    rows.append((f"{y}-{m:02d}-01", px, px, px, px, 1.0))
                    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
                out[s] = rows
            return out

    class DS(FakeDataSource):
        alpaca = Alp()

    monkeypatch.setattr(main, "make_datasource", lambda: DS())
    monkeypatch.setattr(ss, "_today", lambda: d(2026, 11, 8))
    for t in ["UPMAR", "FLAT"]:
        db.add(Security(ticker=t, cik="1", name=t, exchange="Nasdaq"))
    db.commit()
    h = _auth(client)
    r = client.post("/api/seasonality/load", headers=h, json={"max_seconds": 120}).json()
    assert r["remaining"] == 0 and r["loaded"] == 2 and calls[0][1] == "1Month" and r["coverage"]["symbols"] == 2
    mar = client.get("/api/seasonality?month=3&min_years=5", headers=h).json()
    assert mar["ranked"][0]["symbol"] == "UPMAR" and mar["ranked"][0]["pct"] == 100.0 and mar["ranked"][0]["years"] == 10
    n = len(calls)
    again = client.post("/api/seasonality/load", headers=h, json={}).json()
    assert again["processed"] == 0 and len(calls) == n  # nothing new until next month


def test_seasonality_backfills_bullish_bearish_columns(client, db):
    from app.models import SeasonalityStat

    h = _auth(client)
    client.post("/api/seasonality/import", headers=h, json={"rows": _month_rows(), "replace_all": True})
    client.post("/api/seasonality/import/finish", headers=h, json={"last_date": "2025-12-31"})
    db.query(SeasonalityStat).update({"pos_years": None, "neg_years": None})
    db.commit()
    t = client.get("/api/seasonality?month=11&min_years=5&index=all", headers=h).json()
    assert t["ranked"][0]["symbol"] == "GOOD" and t["ranked"][0]["count"] == 11


def test_seasonality_price_column_uses_live_snapshot(client, db, monkeypatch):
    import app.main as main
    from tests.conftest import FakeDataSource

    class Alp:
        async def multi_snapshots(self, symbols):
            return {"GOOD": {"price": 123.45, "market_ts": None}}

    class DS(FakeDataSource):
        alpaca = Alp()

    monkeypatch.setattr(main, "make_datasource", lambda: DS())
    h = _auth(client)
    client.post("/api/seasonality/import", headers=h, json={"rows": _month_rows(), "replace_all": True})
    client.post("/api/seasonality/import/finish", headers=h, json={"last_date": "2025-12-31"})
    r = {x["symbol"]: x for x in client.get("/api/seasonality?month=11&min_years=5", headers=h).json()["ranked"]}
    assert r["GOOD"]["price"] == 123.45 and r["GOOD"]["price_live"] is True
    assert r["BANK"]["price"] == r["BANK"]["last_close"] and r["BANK"]["price_live"] is False


def test_shares_lookup_uses_sec_and_does_not_cache_timeouts(db):
    import asyncio
    from datetime import date as d

    from app.models import SecFact, Security, SharesOutstanding
    from app.services import research as rs
    from app.providers.http import ProviderError

    db.add_all([Security(ticker="SECX", cik="0000000009", name="Sec X", exchange="Nasdaq"),
                Security(ticker="SLOW", cik="0000000008", name="Slow", exchange="Nasdaq"),
                Security(ticker="NONE", cik="0000000007", name="None", exchange="Nasdaq"),
                SecFact(cik="0000000009", tag="Assets", end=d(2026, 9, 30), val=1e9),
                SecFact(cik="0000000009", tag="CommonStockSharesOutstanding", end=d(2026, 9, 30), val=5e7)])
    db.commit()

    class DS:
        async def shares_outstanding(self, t):
            if t == "SLOW":
                await asyncio.sleep(60)
            raise ProviderError("all", "no data")

    r = asyncio.run(rs.load_shares(db, DS(), ["SECX", "SLOW", "NONE"], max_seconds=0.5))
    got = {x.ticker: (x.shares, x.source) for x in db.query(SharesOutstanding).all()}
    assert got == {"SECX": (5e7, "sec"), "NONE": (None, None)}  # SLOW timed out: not cached, tried next batch
    assert r["fetched"] == 1 and r["remaining"] == 1
    rs._save_shares(db, {"SECX": (6e7, "sec")})  # saving an existing ticker again is fine
    assert db.get(SharesOutstanding, "SECX").shares == 6e7


def test_upsert_tolerates_existing_rows_and_analyst_budget(db):
    import asyncio
    from datetime import datetime, timezone

    from app.db import upsert
    from app.models import AnalystSnapshot
    from app.services import research as rs

    now = datetime.now(timezone.utc)
    row = {"symbol": "AAA", "strong_buy": 1, "buy": 0, "hold": 0, "sell": 0, "strong_sell": 0, "provider": "x", "retrieved_at": now}
    upsert(db, AnalystSnapshot, [row])
    upsert(db, AnalystSnapshot, [row | {"strong_buy": 5}])  # same key again: updated, no duplicate-key error
    db.commit()
    assert db.get(AnalystSnapshot, "AAA").strong_buy == 5

    class Prov:
        provider = "fake"

    class Ad:
        counts = {"strong_buy": 2}

    class DS:
        async def analyst(self, s):
            await asyncio.sleep(0.2)
            return Ad(), Prov()

    r = asyncio.run(rs.load_analyst_counts(db, DS(), [f"S{i}" for i in range(50)], limit=50, max_seconds=0.5))
    assert 1 <= r["fetched"] < 50 and r["remaining"] == 50 - r["fetched"]  # stopped on time, progress saved
    assert db.query(AnalystSnapshot).filter(AnalystSnapshot.symbol.like("S%")).count() == r["fetched"]
